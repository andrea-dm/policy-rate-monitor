"""The steps the command line and the HTTP API share."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from policy_rate_monitor.config import DatasetConfig, FileConfigSource
from policy_rate_monitor.core.models import DataSnapshot, SeriesMetadata, SnapshotStatus
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.service import (
    Observation,
    RefArea,
    StagedData,
    load_staged,
    ref_areas,
    summary,
    timeseries,
)
from tests.fake_reference import FakeReference

CONFIG = Path(__file__).parents[1] / "configs" / "bis_cbpol.json"

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return FileConfigSource(CONFIG).load()


@pytest.fixture
def data(config: DatasetConfig) -> StagedData:
    rows = [
        _row("US", date(2025, 1, 2), "4.5"),
        _row("US", date(2025, 6, 2), "4.25", valid=False),
        _row("US", date(2026, 1, 2), "3.75"),
        _row("XM", date(2025, 1, 2), "3"),
        _row("XM", date(2026, 1, 2), "2"),
    ]
    series = [
        SeriesMetadata(
            series_key=f"D.{ref_area}",
            ref_area=ref_area,
            ref_area_name=name,
            frequency="D",
            title=f"Policy rate of {name}",
        )
        for ref_area, name in (("XM", "Euro area"), ("US", "United States"))
    ]
    return load_staged(InMemoryProvider(config.structure, rows, series))


def _row(
    ref_area: str, day: date, value: str, *, valid: bool = True
) -> dict[str, object]:
    return {
        "series_key": f"D.{ref_area}",
        "freq": "D",
        "ref_area": ref_area,
        "date": day,
        "value": Decimal(value),
        "obs_status": "A",
        "is_valid": valid,
    }


def test_ref_areas_are_listed_by_code_with_their_aliases(
    config: DatasetConfig, data: StagedData
) -> None:
    assert ref_areas(config, data) == [
        RefArea(ref_area="US", ref_area_name="United States"),
        RefArea(ref_area="XM", ref_area_name="Euro area", aliases=("EA",)),
    ]


def test_timeseries_keeps_the_valid_observations_exactly(
    config: DatasetConfig, data: StagedData
) -> None:
    found = timeseries(config, data, "US")

    assert found is not None
    assert found.series.series_key == "D.US"
    assert found.observations == (
        Observation(date=date(2025, 1, 2), value=Decimal("4.5")),
        Observation(date=date(2026, 1, 2), value=Decimal("3.75")),
    )


def test_timeseries_starts_at_the_start_date(
    config: DatasetConfig, data: StagedData
) -> None:
    found = timeseries(config, data, "US", start=date(2025, 1, 3))

    assert found is not None
    assert [item.date for item in found.observations] == [date(2026, 1, 2)]


def test_timeseries_reads_an_alias_and_keeps_the_code_as_typed(
    config: DatasetConfig, data: StagedData
) -> None:
    found = timeseries(config, data, " ea ")

    assert found is not None
    assert found.requested_ref_area == "ea"
    assert found.series.ref_area == "XM"


def test_timeseries_of_an_unknown_ref_area_is_none(
    config: DatasetConfig, data: StagedData
) -> None:
    assert timeseries(config, data, "UK") is None


def test_summary_gives_one_snapshot_per_requested_code(
    config: DatasetConfig, data: StagedData
) -> None:
    snapshots = summary(config, data, ["US", "EA", "UK", "us"])

    assert [item.requested_ref_area for item in snapshots] == ["US", "EA", "UK"]
    assert [item.status for item in snapshots] == [
        SnapshotStatus.OK,
        SnapshotStatus.OK,
        SnapshotStatus.UNKNOWN_REF_AREA,
    ]
    assert snapshots[0].change_bps == Decimal(-75)


def test_summaries_computed_at_once_from_the_same_data_agree(
    config: DatasetConfig, data: StagedData
) -> None:
    # The API's routes run on a thread pool and share one StagedData.
    expected = summary(config, data, ["US", "EA", "UK"])

    def one(_: int) -> list[DataSnapshot]:
        return summary(config, data, ["US", "EA", "UK"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(64)))

    assert all(result == expected for result in results)


def test_the_staged_series_and_findings_are_tuples(data: StagedData) -> None:
    # Shared across the API's threads, so nothing can be appended to them.
    assert isinstance(data.series, tuple)
    assert isinstance(data.issues, tuple)


# The code list as a fake reference source gives it: the data's codes and two more.
NAMES = {
    "US": "United States",
    "XM": "Euro area",
    "GB": "United Kingdom",
    "BG": "Bulgaria",
}


def checked(
    config: DatasetConfig, data: StagedData, *codes: str
) -> dict[str, tuple[SnapshotStatus, str | None, tuple[str, ...]]]:
    snapshots = summary(config, data, codes, FakeReference(names=NAMES))
    return {
        item.requested_ref_area: (item.status, item.ref_area_name, item.suggestions)
        for item in snapshots
    }


def test_a_code_with_a_series_is_not_changed_by_the_check(
    config: DatasetConfig, data: StagedData
) -> None:
    reference = FakeReference(names=NAMES)

    snapshots = summary(config, data, ["US", "EA"], reference)

    assert snapshots == summary(config, data, ["US", "EA"])
    # No code without a series: the code list is not asked for.
    assert reference.calls == []


def test_a_code_of_the_code_list_with_no_series_gets_its_name(
    config: DatasetConfig, data: StagedData
) -> None:
    assert checked(config, data, "bg") == {
        "bg": (SnapshotStatus.UNKNOWN_REF_AREA, "Bulgaria", ())
    }


@pytest.mark.parametrize(
    ("code", "suggestions"),
    [
        ("GBR", ("GB",)),  # the configured suggestion
        ("uk", ("GB",)),
        ("Bulgaria", ("BG",)),  # the official name, case aside
        ("ZZZ", ()),  # nothing matches
        ("BEL", ()),  # suggested BE, which this code list does not have
    ],
)
def test_a_code_not_in_the_code_list_gets_its_suggestions(
    config: DatasetConfig,
    data: StagedData,
    code: str,
    suggestions: tuple[str, ...],
) -> None:
    assert checked(config, data, code) == {
        code: (SnapshotStatus.UNKNOWN_REF_AREA, None, suggestions)
    }


def test_without_the_code_list_the_codes_are_not_checked(
    config: DatasetConfig, data: StagedData, caplog: pytest.LogCaptureFixture
) -> None:
    unchecked = summary(config, data, ["GBR", "BG"])
    caplog.clear()
    reference = FakeReference(reason="the SDMX API could not be reached")

    snapshots = summary(config, data, ["GBR", "BG"], reference)

    assert snapshots == unchecked
    assert [(item.ref_area_name, item.suggestions) for item in snapshots] == [
        (None, ()),
        (None, ()),
    ]
    assert caplog.messages == [
        (
            "the requested codes were not checked against the code list: "
            "the SDMX API could not be reached"
        )
    ]
