"""Trial: pure Python vs Numba vs Cython on one analytical kernel.

The kernel computes, for every valid observation of every daily series, the change in
basis points against the last valid observation on or before the same calendar date
12 months earlier: a chart-ready history of the 12-month change for all areas. It is
the kind of computation a front end would ask for interactively.

Rates are exact: values are int64 scaled by 10**6 (the measure's scale), so the change
in basis points is (latest - prior) * 100 / 10**6 with no rounding; the kernels return
it scaled by 10**6 as well.

The three versions run the same loop: per series, walk the observations and keep a
second pointer on the prior candidate (both are sorted by date, so one pass suffices).

Usage, from the repository root (numba and cython are trial-only, not dependencies):
    uv run --with numba --with cython python diagnostics/bench_kernels.py [OBSERVATIONS]

OBSERVATIONS defaults to data/staged/observations.parquet (written by
``bis-prates transform``).
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

MONTHS = 12
SCALE = 6
NO_PRIOR = np.iinfo(np.int64).min

CYTHON_SOURCE = """
# cython: boundscheck=False, wraparound=False
import numpy as np

def change_history(int[:] dates, long long[:] values, int[:] targets,
                   long long[:] starts, long long[:] ends):
    cdef Py_ssize_t s, i, j
    out = np.full(values.shape[0], -9223372036854775807 - 1, dtype=np.int64)
    cdef long long[:] result = out
    for s in range(starts.shape[0]):
        j = starts[s]
        for i in range(starts[s], ends[s]):
            while j + 1 < i and dates[j + 1] <= targets[i]:
                j += 1
            if dates[j] <= targets[i]:
                result[i] = (values[i] - values[j]) * 100
    return out
"""


def load(path: Path) -> tuple[np.ndarray, ...]:
    """Valid observations as int arrays, sorted by series then date."""
    frame = pd.read_parquet(path, columns=["series_key", "date", "value", "is_valid"])
    frame = frame[frame["is_valid"]].sort_values(["series_key", "date"])
    dates = pd.to_datetime(frame["date"]).to_numpy().astype("datetime64[D]")
    values = np.array([int(v.scaleb(SCALE)) for v in frame["value"]], dtype=np.int64)
    # Same calendar date `MONTHS` earlier; 29 Feb -> 28 Feb (clip to month end).
    month = dates.astype("datetime64[M]")
    day = dates - month.astype("datetime64[D]")
    target_month = month - np.timedelta64(MONTHS, "M")
    next_month = target_month + np.timedelta64(1, "M")
    month_end = next_month.astype("datetime64[D]") - np.timedelta64(1, "D")
    targets = np.minimum(target_month.astype("datetime64[D]") + day, month_end)
    keys = frame["series_key"].to_numpy()
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]]).astype(np.int64)
    ends = np.r_[starts[1:], len(keys)].astype(np.int64)
    return (
        dates.astype(np.int32),
        values,
        targets.astype(np.int32),
        starts,
        ends,
    )


def change_history_python(dates, values, targets, starts, ends):  # noqa: ANN001, ANN201
    """The kernel in plain Python, on lists."""
    dates, values, targets = dates.tolist(), values.tolist(), targets.tolist()
    result = [NO_PRIOR] * len(values)
    for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
        j = start
        for i in range(start, end):
            while j + 1 < i and dates[j + 1] <= targets[i]:
                j += 1
            if dates[j] <= targets[i]:
                result[i] = (values[i] - values[j]) * 100
    return np.array(result, dtype=np.int64)


def numba_kernel():  # noqa: ANN201
    """The same loop, compiled by Numba on first call."""
    import numba  # noqa: PLC0415

    @numba.njit(cache=False)
    def change_history(dates, values, targets, starts, ends):  # noqa: ANN001, ANN202
        result = np.full(values.shape[0], NO_PRIOR, dtype=np.int64)
        for s in range(starts.shape[0]):
            j = starts[s]
            for i in range(starts[s], ends[s]):
                while j + 1 < i and dates[j + 1] <= targets[i]:
                    j += 1
                if dates[j] <= targets[i]:
                    result[i] = (values[i] - values[j]) * 100
        return result

    return change_history


def cython_kernel():  # noqa: ANN201
    """The same loop in Cython, built with pyximport into a temp dir."""
    import pyximport  # noqa: PLC0415

    build = Path(tempfile.mkdtemp(prefix="bench_kernels_"))
    (build / "cy_kernel.pyx").write_text(CYTHON_SOURCE, encoding="utf-8")
    sys.path.insert(0, str(build))
    pyximport.install(
        build_dir=str(build), setup_args={"include_dirs": np.get_include()}
    )
    import cy_kernel  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    return cy_kernel.change_history


def best_of(runs: int, function, *args) -> tuple[float, np.ndarray]:  # noqa: ANN001
    """Runs the function `runs` times; returns the best time and the last result."""
    times, result = [], None
    for _ in range(runs):
        start = time.perf_counter()
        result = function(*args)
        times.append(time.perf_counter() - start)
    return min(times), result


def main() -> None:
    """Loads the data, runs the three versions and prints the comparison."""
    default = "data/staged/observations.parquet"
    path = Path(sys.argv[1] if len(sys.argv) > 1 else default)
    arrays = load(path)
    print(f"{len(arrays[1]):,} valid observations in {len(arrays[3])} series\n")

    seconds, expected = best_of(1, change_history_python, *arrays)
    rows: list[tuple[str, str, float | None]] = [("pure Python", "-", seconds)]

    for name, make in (("Numba", numba_kernel), ("Cython", cython_kernel)):
        try:
            start = time.perf_counter()
            kernel = make()
            kernel(*arrays)  # first call: Numba compiles here
            setup = time.perf_counter() - start
        except ImportError as error:
            rows.append((name, f"skipped ({error.name} not installed)", None))
            continue
        seconds, result = best_of(5, kernel, *arrays)
        if not np.array_equal(result, expected):
            raise SystemExit(f"{name} gives a different result")
        rows.append((name, f"{setup:.2f} s", seconds))

    print(f"{'version':<12} {'build/compile':>24} {'run (best)':>12} {'speed-up':>9}")
    base = rows[0][2] or 0.0
    for name, setup, seconds in rows:
        if seconds is None:
            print(f"{name:<12} {setup:>24} {'-':>12} {'-':>9}")
        else:
            speedup = base / seconds
            print(f"{name:<12} {setup:>24} {seconds * 1000:>9.1f} ms {speedup:>8.0f}x")
    print("\nAll versions that ran give identical results.")


if __name__ == "__main__":
    main()
