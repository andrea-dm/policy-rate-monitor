"""Contract tests of the core domain models: invariants, immutability, JSON shape."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    DataSnapshot,
    SeriesMetadata,
    SnapshotFlag,
    SnapshotStatus,
    SourceRelease,
)

LATEST = date(2026, 11, 10)
TARGET = date(2025, 11, 10)
PRIOR = date(2025, 11, 7)


def _series(ref_area: str = "CH") -> SeriesMetadata:
    return SeriesMetadata(
        series_key=f"D.{ref_area}",
        ref_area=ref_area,
        ref_area_name="Switzerland",
        frequency="D",
        title="Policy rate, Switzerland, daily",
        unit="Per cent per year",
        decimals=4,
    )


def _ok(**overrides: Any) -> DataSnapshot:
    fields: dict[str, Any] = {
        "requested_ref_area": "ch",
        "ref_area": "CH",
        "series": _series(),
        "latest_date": LATEST,
        "latest_value": Decimal("4.00"),
        "target_date": TARGET,
        "prior_date": PRIOR,
        "prior_value": Decimal("3.75"),
        "change_bps": Decimal(25),
        "status": SnapshotStatus.OK,
    }
    fields.update(overrides)
    return DataSnapshot(**fields)


# --- DataSnapshot: the four statuses ---


@pytest.mark.unit
def test_ok_snapshot_with_every_value_is_valid() -> None:
    snapshot = _ok()

    assert snapshot.status is SnapshotStatus.OK
    assert snapshot.change_bps == Decimal(25)


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing",
    [
        "latest_date",
        "latest_value",
        "target_date",
        "prior_date",
        "prior_value",
        "change_bps",
    ],
)
def test_ok_snapshot_requires_every_value(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        _ok(**{missing: None})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prior_date", "target_date", "latest_date"),
    [
        (TARGET, PRIOR, LATEST),  # prior after target
        (PRIOR, LATEST, TARGET),  # target after latest
    ],
)
def test_ok_snapshot_rejects_unordered_dates(
    prior_date: date, target_date: date, latest_date: date
) -> None:
    with pytest.raises(
        ValidationError, match="prior_date <= target_date <= latest_date"
    ):
        _ok(prior_date=prior_date, target_date=target_date, latest_date=latest_date)


@pytest.mark.unit
def test_ok_snapshot_accepts_prior_on_the_target_date() -> None:
    # "On or before" the target date: equality is the boundary that must pass.
    assert _ok(prior_date=TARGET).prior_date == TARGET


@pytest.mark.unit
def test_ok_snapshot_rejects_a_change_inconsistent_with_the_rates() -> None:
    with pytest.raises(ValidationError, match="change_bps"):
        _ok(change_bps=Decimal("25.01"))


@pytest.mark.unit
def test_insufficient_history_still_reports_the_latest_rate() -> None:
    snapshot = DataSnapshot(
        requested_ref_area="CH",
        ref_area="CH",
        series=_series(),
        latest_date=LATEST,
        latest_value=Decimal("4.00"),
        target_date=TARGET,
        status=SnapshotStatus.INSUFFICIENT_HISTORY,
    )

    assert snapshot.latest_value == Decimal("4.00")
    assert snapshot.prior_value is None
    assert snapshot.change_bps is None


@pytest.mark.unit
def test_insufficient_history_cannot_carry_a_prior_rate() -> None:
    with pytest.raises(ValidationError, match="prior_value"):
        _ok(
            status=SnapshotStatus.INSUFFICIENT_HISTORY, prior_date=None, change_bps=None
        )


@pytest.mark.unit
def test_no_valid_data_carries_no_values() -> None:
    with pytest.raises(ValidationError, match="latest_date"):
        DataSnapshot(
            requested_ref_area="CH",
            ref_area="CH",
            series=_series(),
            latest_date=LATEST,
            status=SnapshotStatus.NO_VALID_DATA,
        )


@pytest.mark.unit
def test_unknown_area_has_neither_series_nor_values() -> None:
    snapshot = DataSnapshot(
        requested_ref_area="ZZ", ref_area="ZZ", status=SnapshotStatus.UNKNOWN_AREA
    )
    assert snapshot.series is None

    with pytest.raises(ValidationError, match="series"):
        DataSnapshot(
            requested_ref_area="ZZ",
            ref_area="ZZ",
            series=_series("ZZ"),
            status=SnapshotStatus.UNKNOWN_AREA,
        )


@pytest.mark.unit
def test_a_known_area_needs_its_series() -> None:
    with pytest.raises(ValidationError, match="series metadata"):
        DataSnapshot(
            requested_ref_area="CH", ref_area="CH", status=SnapshotStatus.NO_VALID_DATA
        )


@pytest.mark.unit
def test_ref_area_must_match_the_series() -> None:
    with pytest.raises(ValidationError, match="differs from the series"):
        _ok(ref_area="XM")


# --- DataSnapshot: flags ---


@pytest.mark.unit
def test_flags_are_sorted_and_deduplicated() -> None:
    snapshot = _ok(
        flags=(
            SnapshotFlag.STALE_PRIOR,
            SnapshotFlag.STALE_LATEST,
            SnapshotFlag.STALE_PRIOR,
        )
    )

    assert snapshot.flags == (SnapshotFlag.STALE_LATEST, SnapshotFlag.STALE_PRIOR)


@pytest.mark.unit
def test_stale_prior_needs_a_prior_observation() -> None:
    with pytest.raises(ValidationError, match="STALE_PRIOR"):
        DataSnapshot(
            requested_ref_area="CH",
            ref_area="CH",
            series=_series(),
            latest_date=LATEST,
            latest_value=Decimal("4.00"),
            target_date=TARGET,
            status=SnapshotStatus.INSUFFICIENT_HISTORY,
            flags=(SnapshotFlag.STALE_PRIOR,),
        )


@pytest.mark.unit
def test_a_flag_does_not_suppress_values() -> None:
    snapshot = _ok(flags=(SnapshotFlag.STALE_LATEST,))

    assert snapshot.status is SnapshotStatus.OK
    assert snapshot.latest_value == Decimal("4.00")


# --- Exact decimals and JSON ---


@pytest.mark.unit
def test_change_is_exact_where_floats_are_not() -> None:
    # A 25 bp hike from 3.85 to 4.10: in floats it is 24.999999999999957.
    assert (4.10 - 3.85) * 100 != 25  # noqa: PLR2004 -- the point of the test
    snapshot = _ok(
        latest_value=Decimal("4.10"),
        prior_value=Decimal("3.85"),
        change_bps=Decimal(25),
    )

    assert snapshot.change_bps == 25  # noqa: PLR2004


@pytest.mark.unit
def test_rates_are_json_numbers_not_strings() -> None:
    payload = json.loads(_ok().model_dump_json())

    assert payload["latest_value"] == 4  # noqa: PLR2004
    assert payload["prior_value"] == 3.75  # noqa: PLR2004
    assert payload["change_bps"] == 25  # noqa: PLR2004
    assert isinstance(payload["change_bps"], float)


@pytest.mark.unit
def test_json_nests_the_series_metadata_and_writes_enums_as_strings() -> None:
    payload = json.loads(_ok(flags=(SnapshotFlag.STALE_PRIOR,)).model_dump_json())

    assert payload["series"]["series_key"] == "D.CH"
    assert payload["status"] == "ok"
    assert payload["flags"] == ["stale_prior"]
    assert payload["latest_date"] == "2026-11-10"


@pytest.mark.unit
def test_python_dump_keeps_decimals() -> None:
    assert _ok().model_dump()["change_bps"] == Decimal(25)


@pytest.mark.unit
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_rates_must_be_finite(value: Decimal) -> None:
    with pytest.raises(ValidationError):
        _ok(latest_value=value)


@given(
    latest=st.decimals(min_value=-10, max_value=200, places=4, allow_nan=False),
    prior=st.decimals(min_value=-10, max_value=200, places=4, allow_nan=False),
)
@pytest.mark.unit
def test_any_four_decimal_rates_give_an_exact_change(
    latest: Decimal, prior: Decimal
) -> None:
    change = (latest - prior) * 100
    snapshot = _ok(latest_value=latest, prior_value=prior, change_bps=change)

    # Four-decimal rates give basis points with at most two decimals, so rounding to
    # 0.01 changes nothing. (The exponent alone would mislead: Decimal keeps trailing
    # zeros, so 0.0001 * 100 is 0.0100.)
    assert snapshot.change_bps == change
    assert change.quantize(Decimal("0.01")) == change
    # The JSON number is the closest float to the exact value.
    assert json.loads(snapshot.model_dump_json())["change_bps"] == float(change)


# --- Immutability and strictness ---


@pytest.mark.unit
def test_models_are_frozen() -> None:
    snapshot = _ok()

    with pytest.raises(ValidationError, match="frozen"):
        snapshot.latest_value = Decimal(5)


@pytest.mark.unit
def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _ok(latest_rate=Decimal(4))


# --- SeriesMetadata, DataQualityIssue, SourceRelease ---


@pytest.mark.unit
def test_series_metadata_keeps_other_attributes() -> None:
    series = SeriesMetadata(
        series_key="D.CH",
        ref_area="CH",
        ref_area_name="Switzerland",
        frequency="D",
        title="Policy rate",
        attributes={"COMPILATION": "SNB policy rate"},
    )

    assert series.attributes == {"COMPILATION": "SNB policy rate"}
    assert series.unit is None


@pytest.mark.unit
def test_data_quality_issue_about_the_whole_dataset() -> None:
    issue = DataQualityIssue(
        rule="sdmx_codelist",
        severity=DataQualitySeverity.WARNING,
        detail="BIS SDMX API unreachable: not evaluated",
        count=0,
    )

    assert issue.series_key is None
    assert json.loads(issue.model_dump_json())["severity"] == "warning"


@pytest.mark.unit
def test_data_quality_issue_count_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        DataQualityIssue(
            rule="excess_decimals",
            severity=DataQualitySeverity.INFO,
            detail="x",
            count=-1,
        )


@pytest.mark.unit
def test_source_release_requires_an_aware_timestamp() -> None:
    naive = datetime(2026, 11, 18)  # noqa: DTZ001 -- naive on purpose

    with pytest.raises(ValidationError, match="timezone"):
        SourceRelease(location="https://example.org/f.zip", retrieved_at=naive)


@pytest.mark.unit
def test_source_release_checksum_is_sha256_hex() -> None:
    release = SourceRelease(
        location="https://example.org/f.zip",
        version_id='"abc123"',
        checksum_sha256="a" * 64,
        retrieved_at=datetime(2026, 11, 18, tzinfo=UTC),
    )
    assert release.version_id == '"abc123"'

    with pytest.raises(ValidationError, match="pattern"):
        SourceRelease(
            location="f.zip", checksum_sha256="ABC", retrieved_at=datetime.now(tz=UTC)
        )
