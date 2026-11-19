"""Contract tests of the core domain models: invariants, immutability, JSON shape."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
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
        attributes={"COMPILATION": "SNB policy rate"},
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


def _insufficient(**overrides: Any) -> DataSnapshot:
    fields: dict[str, Any] = {
        "requested_ref_area": "CH",
        "ref_area": "CH",
        "series": _series(),
        "latest_date": LATEST,
        "latest_value": Decimal("4.00"),
        "target_date": TARGET,
        "status": SnapshotStatus.INSUFFICIENT_HISTORY,
    }
    fields.update(overrides)
    return DataSnapshot(**fields)


def _no_valid_data(**overrides: Any) -> DataSnapshot:
    fields: dict[str, Any] = {
        "requested_ref_area": "CH",
        "ref_area": "CH",
        "series": _series(),
        "status": SnapshotStatus.NO_VALID_DATA,
    }
    fields.update(overrides)
    return DataSnapshot(**fields)


def _unknown_area(**overrides: Any) -> DataSnapshot:
    fields: dict[str, Any] = {
        "requested_ref_area": "ZZ",
        "ref_area": "ZZ",
        "status": SnapshotStatus.UNKNOWN_AREA,
    }
    fields.update(overrides)
    return DataSnapshot(**fields)


# A valid value for every value field, to put one where a status forbids it.
VALUES: dict[str, Any] = {
    "latest_date": LATEST,
    "latest_value": Decimal("4.00"),
    "target_date": TARGET,
    "prior_date": PRIOR,
    "prior_value": Decimal("3.75"),
    "change_bps": Decimal(25),
}


# --- DataSnapshot: what each status carries -----------------------


@pytest.mark.unit
@pytest.mark.parametrize("missing", list(VALUES))
def test_ok_snapshot_requires_every_value(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        _ok(**{missing: None})


@pytest.mark.unit
def test_ok_snapshot_accepts_prior_on_the_target_date() -> None:
    # "On or before" the target date: equality is the boundary that must pass.
    assert _ok(prior_date=TARGET).prior_date == TARGET


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
def test_ok_snapshot_rejects_a_change_inconsistent_with_the_rates() -> None:
    inconsistent = Decimal("25.01")

    with pytest.raises(ValidationError, match="change_bps"):
        _ok(change_bps=inconsistent)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"latest_value": None}, "needs latest_value"),
        ({"prior_value": Decimal("3.75")}, "cannot have prior_value"),
        ({"change_bps": Decimal(25)}, "cannot have change_bps"),
    ],
    ids=["needs_latest", "no_prior", "no_change"],
)
def test_insufficient_history_has_the_latest_rate_and_no_prior(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _insufficient(**overrides)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("build", "field", "value"),
    [
        (_no_valid_data, "latest_value", Decimal("4.00")),
        (_unknown_area, "latest_date", LATEST),
        (_unknown_area, "series", _series("ZZ")),
    ],
    ids=["no_valid_data", "unknown_area_value", "unknown_area_series"],
)
def test_snapshot_without_data_carries_no_values(
    build: Any, field: str, value: object
) -> None:
    with pytest.raises(ValidationError, match=f"cannot have {field}"):
        build(**{field: value})


@pytest.mark.unit
@pytest.mark.parametrize("ref_area", ["XM", "ch"])
def test_ref_area_must_match_the_series_exactly(ref_area: str) -> None:
    with pytest.raises(ValidationError, match="differs from the series"):
        _ok(ref_area=ref_area)


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


# --- Exact decimals and JSON --------------------------------------


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
@pytest.mark.parametrize("value", [4.1, "4.1"])
def test_python_callers_must_pass_decimals(value: object) -> None:
    # A float would carry its binary rounding into a value treated as exact.
    with pytest.raises(ValidationError, match="instance of Decimal"):
        _insufficient(latest_value=value)


@pytest.mark.unit
def test_json_writes_rates_as_numbers_and_enums_as_strings() -> None:
    payload = json.loads(_ok(flags=(SnapshotFlag.STALE_PRIOR,)).model_dump_json())

    assert payload["latest_value"] == 4  # noqa: PLR2004
    assert payload["prior_value"] == 3.75  # noqa: PLR2004
    assert isinstance(payload["change_bps"], float)
    assert payload["status"] == "ok"
    assert payload["flags"] == ["stale_prior"]
    assert payload["latest_date"] == "2026-11-10"
    assert payload["series"]["attributes"] == {"COMPILATION": "SNB policy rate"}


@pytest.mark.unit
def test_every_model_round_trips_through_its_own_json() -> None:
    models = [
        _ok(flags=(SnapshotFlag.STALE_PRIOR,)),
        _insufficient(),
        _no_valid_data(),
        _unknown_area(),
        DataQualityIssue(
            rule="excess_decimals", severity=DataQualitySeverity.WARNING, detail="x"
        ),
        SourceRelease(
            location="https://example.org/f.zip",
            version_id='"abc"',
            checksum_sha256="a" * 64,
            retrieved_at=datetime(2026, 11, 18, 9, 30, tzinfo=UTC),
        ),
    ]

    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


# --- Immutability -------------------------------------------------


@pytest.mark.unit
def test_models_are_frozen() -> None:
    snapshot = _ok()
    attributes: Any = snapshot.series.attributes if snapshot.series else {}

    with pytest.raises(ValidationError, match="frozen"):
        snapshot.latest_value = Decimal(5)
    with pytest.raises(TypeError, match="does not support item assignment"):
        attributes["COMPILATION"] = "changed"


# --- SourceRelease ------------------------------------------------


@pytest.mark.unit
def test_source_release_requires_an_aware_timestamp() -> None:
    naive = datetime(2026, 11, 18)  # noqa: DTZ001 -- naive on purpose

    with pytest.raises(ValidationError, match="timezone"):
        SourceRelease(location="https://example.org/f.zip", retrieved_at=naive)


@pytest.mark.unit
@pytest.mark.parametrize(
    "checksum",
    ["A" * 64, "a" * 63, "g" * 64],
    ids=["upper", "short", "not_hex"],
)
def test_source_release_checksum_is_64_lowercase_hex_digits(checksum: str) -> None:
    now = datetime.now(tz=UTC)

    with pytest.raises(ValidationError, match="pattern"):
        SourceRelease(location="f.zip", checksum_sha256=checksum, retrieved_at=now)
