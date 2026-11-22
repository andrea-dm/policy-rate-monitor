"""Times the analytics kernels on the real staged data. A benchmark, not a test.

Run ``bis-prates fetch`` and ``bis-prates transform`` first, then:

    uv run python diagnostics/bench_analytics.py [DATA_DIR] [--config PATH]

With the ``fast`` extra installed (numba), the compiled kernels are timed too. It
times, for every series:

1. the v0.1.0 method (Python lists and ``bisect`` per series) at a sample of 25 of
   250 as-of dates, as a date slider would ask;
2. ``snapshot_at`` at the 250 dates, with the NumPy and then the numba kernels (the
   first numba call, which compiles them, is timed on its own);
3. ``change_history`` NumPy against numba, and ``rate_moves`` (NumPy only);
4. a ``Decimal`` loop doing what ``change_history`` does;
5. ``compute_snapshots`` for every country or area, the call the report makes.

Each fast result is checked against the slow one it replaces.
"""

# The benchmark swaps the private kernels to time both versions in one process.
# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false

from __future__ import annotations

import argparse
import time
from bisect import bisect_right
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from policy_rate_monitor import analytics
from policy_rate_monitor.analytics import (
    NO_INDEX,
    bps,
    change_history,
    day_number,
    rate_history,
    rate_moves,
    snapshot_at,
)
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.snapshot import SnapshotArgs, compute_snapshots, months_before
from policy_rate_monitor.transform import load_observations, load_series

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from decimal import Decimal

    import pandas as pd

    from policy_rate_monitor.analytics import RateHistory
    from policy_rate_monitor.config.models import SnapshotSection
    from policy_rate_monitor.core.models import SeriesMetadata

STEPS = 250
SLOW_STEPS = 25  # the v0.1.0 method takes about a second per step
TIME_LIMIT = 60.0  # seconds the Decimal loop may run before it extrapolates

Expected = tuple["date | None", "date | None", "Decimal | None"]
Row = tuple[str, float, str]

COMPILED_KERNELS: dict[str, Callable[..., object]] = {
    "_prior_index": analytics._prior_index,
    "_last_on_or_before": analytics._last_on_or_before,
}
NUMPY_KERNELS: dict[str, Callable[..., object]] = {
    "_prior_index": analytics._prior_index_numpy,
    "_last_on_or_before": analytics._last_on_or_before_numpy,
}


def use_kernels(kernels: dict[str, Callable[..., object]]) -> None:
    """Swaps the kernels the analytics functions call (benchmark only)."""
    for name, kernel in kernels.items():
        setattr(analytics, name, kernel)


def best_of(repeat: int, run: Callable[[], object]) -> float:
    """Returns the shortest of *repeat* timed runs, in seconds."""
    times: list[float] = []
    for _ in range(repeat):
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    return min(times)


def per_call(label: str, seconds: float, calls: int, unit: str) -> Row:
    """A table row for *calls* timed calls, with the time of one call."""
    return (label, seconds, f"{1e6 * seconds / calls:.1f} us {unit}")


def bisect_snapshots(
    observations: pd.DataFrame, keys: tuple[str, ...], months: int
) -> dict[str, Expected]:
    """The v0.1.0 method: latest, prior and change per series with ``bisect``."""
    valid = observations[observations["is_valid"]]
    result: dict[str, Expected] = {}
    for key in keys:
        rows = valid[valid["series_key"] == key].sort_values("date")
        dates: list[date] = rows["date"].tolist()
        values: list[Decimal] = rows["value"].tolist()
        if not dates:
            result[key] = (None, None, None)
            continue
        prior = bisect_right(dates, months_before(dates[-1], months)) - 1
        if prior < 0:
            result[key] = (dates[-1], None, None)
        else:
            change = (values[-1] - values[prior]) * 100
            result[key] = (dates[-1], dates[prior], change)
    return result


def fast_snapshots(
    history: RateHistory, as_of: date, months: int
) -> dict[str, Expected]:
    """What ``snapshot_at`` finds, in the form of ``bisect_snapshots``."""
    arrays = snapshot_at(history, day_number(as_of), months)
    result: dict[str, Expected] = {}
    for i, key in enumerate(history.keys):
        latest, prior = int(arrays.latest[i]), int(arrays.prior[i])
        if latest == NO_INDEX:
            result[key] = (None, None, None)
        elif prior == NO_INDEX:
            result[key] = (history.date_at(latest), None, None)
        else:
            change = bps(arrays.change[i], history.scale)
            result[key] = (history.date_at(latest), history.date_at(prior), change)
    return result


def slider_dates(observations: pd.DataFrame) -> list[date]:
    """250 as-of dates from 400 days after the first date to the last one."""
    first: date = observations["date"].min() + timedelta(days=400)
    last: date = observations["date"].max()
    span = (last - first).days
    return [first + timedelta(days=round(i * span / (STEPS - 1))) for i in range(STEPS)]


def bench_v010(
    observations: pd.DataFrame, history: RateHistory, months: int
) -> tuple[Row, str]:
    """Times the v0.1.0 method on a sample of the slider dates and checks agreement."""
    sample = slider_dates(observations)[:: STEPS // SLOW_STEPS]
    elapsed = 0.0
    mismatches = 0
    for as_of in sample:
        start = time.perf_counter()
        visible = observations[observations["date"] <= as_of]
        expected = bisect_snapshots(visible, history.keys, months)
        elapsed += time.perf_counter() - start
        got = fast_snapshots(history, as_of, months)
        mismatches += sum(got[key] != expected[key] for key in history.keys)
    one_step = elapsed / len(sample)
    row = (
        f"v0.1.0 method (bisect), {STEPS} steps (from {len(sample)})",
        one_step * STEPS,
        f"{1e3 * one_step:.0f} ms per step; extrapolated",
    )
    check = (
        f"snapshot_at vs the v0.1.0 method: {mismatches} mismatches over "
        f"{len(sample)} dates x {len(history.keys)} series"
    )
    return row, check


def bench_kernels(
    history: RateHistory, as_of_days: list[int], months: int, name: str
) -> list[Row]:
    """Times the slider, one search and change_history."""
    limits = np.full(len(history.keys), as_of_days[-1], dtype=np.int32)

    def slider() -> None:
        for as_of in as_of_days:
            snapshot_at(history, as_of, months)

    def search_only() -> None:  # one of the two searches of a slider step
        for _ in range(STEPS):
            analytics._last_on_or_before(history.series_days, history.offsets, limits)

    return [
        per_call(
            f"snapshot_at slider, {name}, {STEPS} steps",
            best_of(5, slider),
            STEPS,
            "per step",
        ),
        per_call(
            f"  one search for all series, {name}, {STEPS} times",
            best_of(5, search_only),
            STEPS,
            "each",
        ),
        (
            f"change_history, {name}",
            best_of(3, lambda: change_history(history, months)),
            "includes months_back",
        ),
    ]


def decimal_change_history(
    observations: pd.DataFrame, months: int
) -> tuple[dict[str, list[Decimal | None]], int, float]:
    """The change history with Python dates, Decimals and bisect, series by series.

    Returns:
        The changes in basis points per series key (None where there is no prior
        observation), the rows done and the seconds taken. It stops after the
        first series that ends past the time limit.
    """
    start = time.perf_counter()
    valid = observations[observations["is_valid"]]
    changes: dict[str, list[Decimal | None]] = {}
    rows = 0
    for key in sorted(str(key) for key in valid["series_key"].unique()):
        rows_of_key = valid[valid["series_key"] == key].sort_values("date")
        dates: list[date] = rows_of_key["date"].tolist()
        values: list[Decimal] = rows_of_key["value"].tolist()
        series_changes: list[Decimal | None] = []
        for day, value in zip(dates, values, strict=True):
            prior = bisect_right(dates, months_before(day, months)) - 1
            if prior < 0:
                series_changes.append(None)
            else:
                series_changes.append((value - values[prior]) * 100)
        changes[key] = series_changes
        rows += len(dates)
        if time.perf_counter() - start > TIME_LIMIT:
            break
    return changes, rows, time.perf_counter() - start


def bench_decimal_loop(
    observations: pd.DataFrame, history: RateHistory, months: int
) -> tuple[Row, str]:
    """Times the Decimal loop and checks change_history against it."""
    exact, done, seconds = decimal_change_history(observations, months)
    total = len(history.days)
    note = "measured" if done == total else f"extrapolated from {done:,} rows"
    row = ("change_history, Python Decimal loop", seconds * total / done, note)

    result = change_history(history, months)
    mismatches = 0
    for key, changes in exact.items():
        begin = int(history.offsets[history.position(key)])
        for offset, expected in enumerate(changes):
            got = None
            if result.prior[begin + offset] >= 0:
                got = bps(result.change[begin + offset], history.scale)
            mismatches += got != expected
    check = (
        f"change_history vs Decimal loop: {mismatches} mismatches over {done:,} rows"
    )
    return row, check


def bench_numba(history: RateHistory, as_of_days: list[int], months: int) -> list[Row]:
    """Times the first call of the compiled kernels, then the kernels themselves."""
    use_kernels(COMPILED_KERNELS)
    start = time.perf_counter()
    snapshot_at(history, as_of_days[0], months)
    change_history(history, months)
    first_call = (
        "numba first call of the 2 kernels",
        time.perf_counter() - start,
        "compiles, or loads from the disk cache after the first run",
    )
    return [first_call, *bench_kernels(history, as_of_days, months, "numba")]


def bench_report(
    observations: pd.DataFrame,
    series: list[SeriesMetadata],
    settings: SnapshotSection,
) -> Row:
    """Times compute_snapshots for every country or area, the call the report makes."""
    args = SnapshotArgs(
        observations=observations,
        series=series,
        requested=[item.ref_area for item in series],
        settings=settings,
        ref_area_aliases={},
    )
    return (
        f"compute_snapshots, all {len(series)} countries or areas (the report's call)",
        best_of(3, lambda: compute_snapshots(args)),
        "",
    )


def main() -> None:
    """Runs the benchmark and prints a Markdown table."""
    parser = argparse.ArgumentParser(description="Times the analytics kernels.")
    parser.add_argument("data_dir", nargs="?", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("configs/bis_cbpol.json"))
    args = parser.parse_args()
    config = FileConfigSource(args.config).load()
    months = config.snapshot.lookback_months
    observations = load_observations(args.data_dir)
    series = load_series(args.data_dir)

    start = time.perf_counter()
    history = rate_history(observations)
    rows: list[Row] = [("rate_history (built once)", time.perf_counter() - start, "")]
    slow_row, slider_check = bench_v010(observations, history, months)
    rows.append(slow_row)

    as_of_days = [day_number(day) for day in slider_dates(observations)]
    use_kernels(NUMPY_KERNELS)
    rows += bench_kernels(history, as_of_days, months, "NumPy")
    rows.append(
        ("rate_moves (NumPy only)", best_of(3, lambda: rate_moves(history)), "")
    )
    if analytics.NUMBA_AVAILABLE:
        rows += bench_numba(history, as_of_days, months)
    decimal_row, decimal_check = bench_decimal_loop(observations, history, months)
    rows.append(decimal_row)

    rows.append(bench_report(observations, series, config.snapshot))

    print(f"numba available: {analytics.NUMBA_AVAILABLE}")
    print(f"series: {len(history.keys)}, valid observations: {len(history.days):,}")
    print(f"rate moves: {len(rate_moves(history).index):,}\n")
    print("| Step | Time | Note |")
    print("|---|---:|---|")
    for label, seconds, note in rows:
        print(f"| {label} | {seconds:.4f} s | {note} |")
    print(f"\n{slider_check}\n{decimal_check}")


if __name__ == "__main__":
    main()
