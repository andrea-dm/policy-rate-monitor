from __future__ import annotations

import subprocess
import sys
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest
from pandas import ArrowDtype, DataFrame, Series
from pyarrow import decimal128

from policy_rate_monitor import analytics
from policy_rate_monitor.analytics import (
    NO_INDEX,
    RateHistory,
    bps,
    change_history,
    day_number,
    rate_history,
    rate_moves,
    snapshot_at,
)
from policy_rate_monitor.snapshot import months_before

pytestmark = pytest.mark.unit

SCALE = 6
RATE = ArrowDtype(decimal128(15, SCALE))
MONTHS = 12

# (ref_area, date, value, is_valid); a None value is a missing observation.
Row = tuple[str, str, str | None, bool]

ROWS: list[Row] = [
    # A target on a Saturday, 29 February, and invalid rows to skip.
    ("US", "2023-02-28", "4.50", True),
    ("US", "2023-03-01", "4.60", True),  # where 29 Feb would land without clamping
    ("US", "2023-03-03", "4.75", True),
    ("US", "2023-03-06", "9.99", False),
    ("US", "2024-02-29", "5.50", True),
    ("US", "2024-03-04", "5.25", True),
    ("US", "2024-03-05", None, False),
    # Negative rates, history before 1970; the last observation is invalid.
    ("CH", "1969-06-30", "-0.75", True),
    ("CH", "1970-01-01", "-0.25", True),
    ("CH", "1970-06-30", "1.00", True),
    ("CH", "1970-07-01", "1.75", True),
    ("CH", "1970-07-02", "2.00", False),
    # Insufficient history until a year has passed.
    ("GB", "2023-08-03", "5.25", True),
    ("GB", "2024-08-01", "5.00", True),
    # No valid observation at all.
    ("XM", "2023-05-04", None, False),
    ("XM", "2024-05-04", "3.75", False),
]


def _observations(rows: list[Row]) -> DataFrame:
    return DataFrame(
        {
            "series_key": [f"D.{ref_area}" for ref_area, _, _, _ in rows],
            "date": Series(
                [date.fromisoformat(day) for _, day, _, _ in rows],
                dtype="date32[pyarrow]",
            ),
            "value": Series(
                [None if value is None else Decimal(value) for _, _, value, _ in rows],
                dtype=RATE,
            ),
            "is_valid": [valid for _, _, _, valid in rows],
        }
    )


Expected = tuple[date | None, date | None, Decimal | None]


def _bisect_snapshot(rows: list[Row], ref_area: str, as_of: date) -> Expected:
    """The v0.1.0 method: Python dates, Decimals and bisect, for one country or area."""
    valid = sorted(
        (date.fromisoformat(day), Decimal(value))
        for row_ref_area, day, value, is_valid in rows
        if row_ref_area == ref_area and is_valid and value is not None
        if date.fromisoformat(day) <= as_of
    )
    if not valid:
        return None, None, None
    dates = [day for day, _ in valid]
    latest_date, latest_value = valid[-1]
    prior = bisect_right(dates, months_before(latest_date, MONTHS)) - 1
    if prior < 0:
        return latest_date, None, None
    prior_date, prior_value = valid[prior]
    return latest_date, prior_date, (latest_value - prior_value) * 100


def _fast_snapshot(history: RateHistory, ref_area: str, as_of: date) -> Expected:
    arrays = snapshot_at(history, day_number(as_of), MONTHS)
    i = history.position(f"D.{ref_area}")
    latest, prior = int(arrays.latest[i]), int(arrays.prior[i])
    if latest == NO_INDEX:
        return None, None, None
    if prior == NO_INDEX:
        return history.date_at(latest), None, None
    return history.date_at(latest), history.date_at(prior), bps(arrays.change[i], SCALE)


def test_snapshot_at_matches_the_bisect_method_at_every_date() -> None:
    history = rate_history(_observations(ROWS))
    # Every date in the data and the day before it, so each step of a slider is hit.
    as_of_dates = {date.fromisoformat(day) for _, day, _, _ in ROWS}
    as_of_dates |= {day - timedelta(days=1) for day in as_of_dates}

    for as_of in sorted(as_of_dates):
        for ref_area in ("US", "CH", "GB", "XM"):
            expected = _bisect_snapshot(ROWS, ref_area, as_of)
            assert _fast_snapshot(history, ref_area, as_of) == expected, (
                ref_area,
                as_of,
            )


def test_values_and_dates_come_back_exactly_as_staged() -> None:
    history = rate_history(_observations(ROWS))
    first_ch = int(history.offsets[history.position("D.CH")])

    assert history.date_at(first_ch) == date(1969, 6, 30)
    assert str(history.value_at(first_ch)) == "-0.750000"


def test_the_history_cannot_be_changed() -> None:
    history = rate_history(_observations(ROWS))

    with pytest.raises(ValueError, match="read-only"):
        history.values[0] = 0


def test_histories_compare_by_identity() -> None:
    # A generated __eq__ would compare the arrays and fail.
    history = rate_history(_observations(ROWS))
    same_rows = rate_history(_observations(ROWS))

    assert history == history  # noqa: PLR0124
    assert history != same_rows
    assert change_history(history, MONTHS) != change_history(history, MONTHS)
    assert rate_moves(history) != rate_moves(history)
    arrays = snapshot_at(history, day_number(date(2024, 3, 4)), MONTHS)
    assert arrays != snapshot_at(history, day_number(date(2024, 3, 4)), MONTHS)


@pytest.mark.whitebox
def test_numpy_and_loop_kernels_give_the_same_arrays() -> None:
    rng = np.random.default_rng(7)
    lengths = [0, 1, 50, 400, 3]
    offsets = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    days = np.concatenate(
        [np.sort(rng.choice(np.arange(-800, 800), n, replace=False)) for n in lengths]
    ).astype(np.int32)
    series = np.repeat(np.arange(len(lengths)), lengths)
    series_days = series * analytics.SERIES_STEP + days
    targets = analytics.months_back(days, MONTHS)
    limits = rng.integers(-900, 900, len(lengths)).astype(np.int32)

    # The loops run as plain Python here, and compiled when numba is installed.
    for prior_index, last_on_or_before in [
        (analytics._prior_index_loop, analytics._last_on_or_before_loop),
        (analytics._prior_index, analytics._last_on_or_before),
    ]:
        assert np.array_equal(
            prior_index(series_days, offsets, targets),
            analytics._prior_index_numpy(series_days, offsets, targets),
        )
        assert np.array_equal(
            last_on_or_before(series_days, offsets, limits),
            analytics._last_on_or_before_numpy(series_days, offsets, limits),
        )


def test_months_back_follows_months_before() -> None:
    days = np.arange(day_number(date(1968, 1, 1)), day_number(date(1973, 1, 1)))

    got = analytics.months_back(days.astype(np.int32), MONTHS)

    epoch = date(1970, 1, 1)
    expected = [
        day_number(months_before(epoch + timedelta(days=int(day)), MONTHS))
        for day in days
    ]
    assert got.tolist() == expected


def test_change_history_is_exact_against_the_same_date_a_year_earlier() -> None:
    us = [row for row in ROWS if row[0] == "US"]
    history = rate_history(_observations(us))

    result = change_history(history, MONTHS)

    # Valid US rows: 2023-02-28, 2023-03-01, 2023-03-03, 2024-02-29, 2024-03-04.
    assert result.prior.tolist() == [-1, -1, -1, 0, 2]
    assert [bps(change, SCALE) for change in result.change[3:]] == [
        Decimal(100),
        Decimal(50),
    ]


def test_rate_moves_skip_invalid_rows_and_series_starts() -> None:
    rows: list[Row] = [
        # Series with no valid row, first and last in key order.
        ("GB", "2024-01-02", None, False),
        ("ZZ", "2024-01-02", "1.00", False),
        ("US", "2024-01-02", "4.50", True),
        ("US", "2024-01-03", "4.50", True),
        ("US", "2024-01-04", "9.99", False),
        ("US", "2024-01-05", "4.75", True),
        ("US", "2024-01-08", "4.25", True),
        ("XM", "2024-01-02", "-0.50", True),
        ("XM", "2024-01-03", "-0.50", True),
    ]
    history = rate_history(_observations(rows))

    moves = rate_moves(history)

    assert [history.date_at(i) for i in moves.index] == [
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    assert [bps(change, SCALE) for change in moves.change] == [
        Decimal(25),
        Decimal(-50),
    ]


@pytest.mark.contract
@pytest.mark.whitebox
def test_without_numba_the_numpy_kernels_are_used() -> None:
    code = (
        "import sys; sys.modules['numba'] = None\n"
        "from policy_rate_monitor import analytics as a\n"
        "assert not a.NUMBA_AVAILABLE\n"
        "assert a._prior_index is a._prior_index_numpy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603
