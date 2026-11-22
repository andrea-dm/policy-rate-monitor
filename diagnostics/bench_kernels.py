"""Trial: pure Python vs Numba vs Cython on one analytical kernel.

The kernel computes, for every valid observation of every daily series, the change in
basis points against the last valid observation on or before the same calendar date
12 months earlier: a chart-ready history of the 12-month change for every country or
area. It is the kind of computation a front end would ask for interactively.

Rates are exact: values are int64 scaled by 10**6 (the measure's scale), so the change
in basis points is (latest - prior) * 100 / 10**6 with no rounding; the kernels return
it scaled by 10**6 as well.

The three versions run the same loop: per series, walk the observations and keep a
second pointer on the prior candidate (both are sorted by date, so one pass suffices).
The Cython version is written at C level: contiguous typed memoryviews with every
check off, the loop in a ``nogil`` function, and the same compiler target as Numba's
(``-O3 -march=native``). NumPy only allocates the result, with ``np.empty``, in both the
Numba and the Cython version: each loop writes every element once. Filling it first
with ``np.full`` cost about a quarter of the run (4.5 ms against 3.3 ms with Numba).

Usage, from the repository root (Numba comes with the ``fast`` extra, Cython with the
``diagnostics`` group; Cython also needs a C compiler):
    uv run python diagnostics/bench_kernels.py [OBSERVATIONS]

OBSERVATIONS defaults to data/staged/observations.parquet (written by
``bis-prates transform``).
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from policy_rate_monitor.analytics import DAY

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

    type Days = NDArray[np.int32]
    type Ints = NDArray[np.int64]
    # dates, values, targets, starts, ends
    type Arrays = tuple[Days, Ints, Days, Ints, Ints]
    type Kernel = Callable[[Days, Ints, Days, Ints, Ints], Ints]

MONTHS = 12
SCALE = 6
NO_PRIOR = np.iinfo(np.int64).min

CYTHON_SOURCE = """
# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False
from libc.stdint cimport INT64_MIN, int32_t, int64_t

import numpy as np


cdef void fill(const int32_t[::1] dates, const int64_t[::1] values,
               const int32_t[::1] targets, const int64_t[::1] starts,
               const int64_t[::1] ends, int64_t[::1] result) noexcept nogil:
    cdef Py_ssize_t s, i, j
    for s in range(starts.shape[0]):
        j = starts[s]
        for i in range(starts[s], ends[s]):
            while j + 1 < i and dates[j + 1] <= targets[i]:
                j += 1
            if dates[j] <= targets[i]:
                result[i] = (values[i] - values[j]) * 100
            else:
                result[i] = INT64_MIN


def change_history(const int32_t[::1] dates, const int64_t[::1] values,
                   const int32_t[::1] targets, const int64_t[::1] starts,
                   const int64_t[::1] ends):
    out = np.empty(values.shape[0], dtype=np.int64)
    cdef int64_t[::1] result = out
    with nogil:
        fill(dates, values, targets, starts, ends, result)
    return out
"""

# pyximport reads the compiler flags from this file, next to the .pyx.
CYTHON_BUILD = """
from setuptools import Extension


def make_ext(modname, pyxfilename):
    flags = ["-O3", "-march=native"]
    return Extension(modname, [pyxfilename], extra_compile_args=flags)
"""


def load(path: Path) -> Arrays:
    """Valid observations as int arrays, sorted by series then date."""
    frame = pd.read_parquet(path, columns=["series_key", "date", "value", "is_valid"])
    frame = frame[frame.is_valid].sort_values(["series_key", "date"])
    dates = pd.to_datetime(frame.date).to_numpy().astype(DAY)
    values = np.array([int(v.scaleb(SCALE)) for v in frame.value], dtype=np.int64)
    # Same calendar date `MONTHS` earlier; 29 Feb -> 28 Feb (clip to month end).
    month = dates.astype("datetime64[M]")
    day = dates - month.astype(DAY)
    target_month = month - np.timedelta64(MONTHS, "M")
    next_month = target_month + np.timedelta64(1, "M")
    month_end = next_month.astype(DAY) - np.timedelta64(1, "D")
    targets = np.minimum(target_month.astype(DAY) + day, month_end)
    keys = frame.series_key.to_numpy()
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]]).astype(np.int64)
    ends = np.r_[starts[1:], len(keys)].astype(np.int64)
    return (
        dates.astype(np.int32),
        values,
        targets.astype(np.int32),
        starts,
        ends,
    )


def change_history_python(
    dates: Days, values: Ints, targets: Days, starts: Ints, ends: Ints
) -> Ints:
    """The kernel in plain Python, on lists."""
    day, value, target = dates.tolist(), values.tolist(), targets.tolist()
    result = [int(NO_PRIOR)] * len(value)
    for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
        j = start
        for i in range(start, end):
            while j + 1 < i and day[j + 1] <= target[i]:
                j += 1
            if day[j] <= target[i]:
                result[i] = (value[i] - value[j]) * 100
    return np.array(result, dtype=np.int64)


def numba_kernel() -> Kernel:
    """The same loop, compiled by Numba on first call."""
    import numba  # noqa: PLC0415

    @numba.njit(cache=False)
    def change_history(
        dates: Days, values: Ints, targets: Days, starts: Ints, ends: Ints
    ) -> Ints:
        result = np.empty(values.shape[0], dtype=np.int64)
        for s in range(starts.shape[0]):
            j = starts[s]
            for i in range(starts[s], ends[s]):
                while j + 1 < i and dates[j + 1] <= targets[i]:
                    j += 1
                if dates[j] <= targets[i]:
                    result[i] = (values[i] - values[j]) * 100
                else:
                    result[i] = NO_PRIOR
        return result

    return change_history


def cython_kernel() -> Kernel:
    """The same loop in Cython, built with pyximport into a temporary folder."""
    import pyximport  # noqa: PLC0415

    build = Path(tempfile.mkdtemp(prefix="bench_kernels_"))
    (build / "cy_kernel.pyx").write_text(CYTHON_SOURCE, encoding="utf-8")
    (build / "cy_kernel.pyxbld").write_text(CYTHON_BUILD, encoding="utf-8")
    sys.path.insert(0, str(build))
    pyximport.install(build_dir=str(build), language_level=3)
    # The module exists only once pyximport has built it, so it is imported by name.
    kernel: Kernel = importlib.import_module("cy_kernel").change_history
    return kernel


def best_of(runs: int, kernel: Kernel, arrays: Arrays) -> tuple[float, Ints]:
    """Runs the kernel `runs` times; returns the best time and the last result."""
    times: list[float] = []
    result = arrays[1]
    for _ in range(runs):
        start = time.perf_counter()
        result = kernel(*arrays)
        times += [time.perf_counter() - start]
    return min(times), result


def main() -> None:
    """Loads the data, runs the three versions and prints the comparison."""
    default = "data/staged/observations.parquet"
    arrays = load(Path(sys.argv[1] if len(sys.argv) > 1 else default))
    print(f"{len(arrays[1]):,} valid observations in {len(arrays[3])} series\n")

    base, expected = best_of(1, change_history_python, arrays)
    rows: list[tuple[str, str, float]] = [("pure Python", "-", base)]
    for name, make in (("Numba", numba_kernel), ("Cython", cython_kernel)):
        start = time.perf_counter()
        kernel = make()
        kernel(*arrays)  # first call: Numba compiles here
        setup = time.perf_counter() - start
        seconds, result = best_of(5, kernel, arrays)
        if not np.array_equal(result, expected):
            raise SystemExit(f"{name} gives a different result")
        rows += [(name, f"{setup:.2f} s", seconds)]

    print(f"{'version':<12} {'build/compile':>14} {'run (best)':>12} {'speed-up':>9}")
    for name, setup, seconds in rows:
        speed_up = base / seconds
        print(f"{name:<12} {setup:>14} {seconds * 1000:>9.1f} ms {speed_up:>8.0f}x")
    print("\nAll versions give identical results.")


if __name__ == "__main__":
    main()
