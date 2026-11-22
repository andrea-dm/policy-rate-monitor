from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pytest

from policy_rate_monitor.config.models import SnapshotSection
from policy_rate_monitor.core.models import (
    DataSnapshot,
    SeriesMetadata,
    SnapshotFlag,
    SnapshotStatus,
)
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots

pytestmark = pytest.mark.unit

SETTINGS = SnapshotSection(lookback_months=12, stale_latest_days=30, stale_prior_days=7)
ALIASES = {"EA": "XM"}
NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CH": "Switzerland",
    "XM": "Euro area",
}

# The staged value type (precision and scale from the config's measure).
RATE = pd.ArrowDtype(pa.decimal128(15, 6))

# (ref_area, date, value, is_valid); a None value is a missing observation.
Row = tuple[str, str, str | None, bool]


def _observations(rows: list[Row]) -> pd.DataFrame:
    """Builds staged observations shaped like the transform's output."""
    return pd.DataFrame(
        {
            "series_key": [f"D.{ref_area}" for ref_area, _, _, _ in rows],
            "freq": ["D"] * len(rows),
            "ref_area": [ref_area for ref_area, _, _, _ in rows],
            "date": pd.Series(
                [date.fromisoformat(day) for _, day, _, _ in rows],
                dtype="date32[pyarrow]",
            ),
            "value": pd.Series(
                [None if value is None else Decimal(value) for _, _, value, _ in rows],
                dtype=RATE,
            ),
            "is_valid": [valid for _, _, _, valid in rows],
        }
    )


def _series(*ref_areas: str) -> list[SeriesMetadata]:
    return [
        SeriesMetadata(
            series_key=f"D.{ref_area}",
            ref_area=ref_area,
            ref_area_name=NAMES[ref_area],
            frequency="D",
            title=f"Policy rate, {NAMES[ref_area]}, daily",
        )
        for ref_area in ref_areas
    ]


def _snapshots(rows: list[Row], requested: list[str]) -> list[DataSnapshot]:
    ref_areas = sorted({ref_area for ref_area, _, _, _ in rows})
    args = SnapshotArgs(
        observations=_observations(rows),
        series=_series(*ref_areas),
        requested=requested,
        settings=SETTINGS,
        ref_area_aliases=ALIASES,
    )
    return compute_snapshots(args)


def test_change_is_exact_in_basis_points() -> None:
    rows: list[Row] = [
        ("US", "2025-11-10", "3.75", True),
        ("US", "2026-11-10", "4.00", True),
    ]

    [snapshot] = _snapshots(rows, ["US"])

    assert snapshot.status is SnapshotStatus.OK
    assert snapshot.target_date == date(2025, 11, 10)
    assert snapshot.prior_date == date(2025, 11, 10)
    assert snapshot.change_bps == Decimal(25)
    assert snapshot.flags == ()


def test_target_on_a_weekend_takes_the_friday_before() -> None:
    rows: list[Row] = [
        ("US", "2025-11-07", "3.50", True),
        ("US", "2025-11-10", "3.75", True),
        ("US", "2026-11-09", "4.00", True),
    ]

    [snapshot] = _snapshots(rows, ["US"])

    assert snapshot.target_date == date(2025, 11, 9)
    assert snapshot.prior_date == date(2025, 11, 7)
    assert snapshot.prior_value == Decimal("3.50")
    assert snapshot.change_bps == Decimal(50)


def test_29_february_looks_back_to_28_february() -> None:
    rows: list[Row] = [
        ("US", "2023-02-28", "4.50", True),
        ("US", "2024-02-29", "5.50", True),
    ]

    [snapshot] = _snapshots(rows, ["US"])

    assert snapshot.target_date == date(2023, 2, 28)
    assert snapshot.prior_date == date(2023, 2, 28)
    assert snapshot.change_bps == Decimal(100)


def test_negative_rates() -> None:
    rows: list[Row] = [
        ("CH", "2025-11-10", "-0.75", True),
        ("CH", "2026-11-10", "0.50", True),
    ]

    [snapshot] = _snapshots(rows, ["CH"])

    assert snapshot.prior_value == Decimal("-0.75")
    assert snapshot.change_bps == Decimal(125)


def test_invalid_observations_are_skipped() -> None:
    rows: list[Row] = [
        ("US", "2025-11-07", "3.50", True),
        ("US", "2025-11-10", "9.99", False),
        ("US", "2026-11-10", "4.00", True),
        ("US", "2026-11-11", None, False),
    ]

    [snapshot] = _snapshots(rows, ["US"])

    assert snapshot.latest_date == date(2026, 11, 10)
    assert snapshot.prior_date == date(2025, 11, 7)


def test_each_ref_area_looks_back_from_its_own_latest_date() -> None:
    rows: list[Row] = [
        ("GB", "2025-11-05", "5.00", True),
        ("GB", "2026-11-05", "4.75", True),
        ("US", "2025-11-10", "3.75", True),
        ("US", "2026-11-10", "4.00", True),
    ]

    gb, us = _snapshots(rows, ["GB", "US"])

    assert (gb.target_date, gb.change_bps) == (date(2025, 11, 5), Decimal(-25))
    assert (us.target_date, us.change_bps) == (date(2025, 11, 10), Decimal(25))


def test_unknown_ref_area_insufficient_history_and_no_valid_data() -> None:
    rows: list[Row] = [
        ("US", "2026-06-01", "3.75", True),
        ("US", "2026-11-10", "4.00", True),
        ("GB", "2026-11-10", None, False),
    ]

    us, gb, zz = _snapshots(rows, ["US", "GB", "ZZ"])

    assert us.status is SnapshotStatus.INSUFFICIENT_HISTORY
    assert us.latest_value == Decimal("4.00")
    assert gb.status is SnapshotStatus.NO_VALID_DATA
    assert zz.status is SnapshotStatus.UNKNOWN_REF_AREA
    assert zz.series is None


def test_ea_is_read_as_the_euro_area_code() -> None:
    rows: list[Row] = [
        ("XM", "2025-11-10", "2.00", True),
        ("XM", "2026-11-10", "2.15", True),
    ]

    [snapshot] = _snapshots(rows, ["EA"])

    assert (snapshot.requested_ref_area, snapshot.ref_area) == ("EA", "XM")
    assert snapshot.change_bps == Decimal(15)


def test_requested_codes_are_cleaned_and_deduplicated() -> None:
    rows: list[Row] = [("US", "2026-11-10", "4.00", True)]

    snapshots = _snapshots(rows, [" us ", "", "GB", "US", "ea", "EA"])

    assert [(s.requested_ref_area, s.ref_area) for s in snapshots] == [
        ("us", "US"),
        ("GB", "GB"),
        ("ea", "XM"),
    ]


def test_stale_latest_and_stale_prior_are_flagged() -> None:
    rows: list[Row] = [
        ("GB", "2025-08-01", "5.00", True),
        ("GB", "2026-09-01", "4.75", True),
        ("US", "2025-11-03", "3.75", True),
        ("US", "2026-11-10", "4.00", True),
    ]

    gb, us = _snapshots(rows, ["GB", "US"])

    assert gb.flags == (SnapshotFlag.STALE_LATEST, SnapshotFlag.STALE_PRIOR)
    assert us.flags == ()
