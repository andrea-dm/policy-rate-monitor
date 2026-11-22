# diagnostics

Exploratory analysis, spikes and benchmarks behind the design decisions. This is not part of the
package: nothing in `src/` imports from here.

The scripts follow the repository's ruff rules, with `print()` allowed because printing is how they
report findings, and pyright's strict mode; Sonar covers them too. tach and deptry do not cover this
folder. What only these scripts import (Cython, DuckDB, and setuptools for building the Cython
kernel) is in the `diagnostics` dependency group, which `uv sync --all-groups` installs; Numba
comes with the `fast` extra.

| File | Purpose |
|---|---|
| `bench_pyarrow_types.py` | Checks the library behaviour decisions 0001 and 0006 rely on: exact `decimal128` arithmetic, the parquet round trip of `decimal128` and a `date32` before 1970, a cast that would drop decimals refused, `merge_asof` refusing Arrow date keys, and pyarrow's `join_asof` finding no match when a key is before 1970. |
| `bench_snapshot.py` | Benchmark of five ways to compute the 12-month look-back for every series, at 1×, 10× and 50× the real data. Evidence for the look-back engine. |
| `bench_kernels.py` | Trial of pure Python, Numba and Cython on one kernel: the 12-month change in basis points for every valid observation of every series, in exact integer arithmetic. Reads `data/staged/observations.parquet` (written by `bis-prates transform`). |
| `check_record_figures.py` | Reproduces the figures about the data quoted in the decision records: the countries or areas whose 12-month change is not exact in floating point (decision 0005), and the highest rate of each country or area (decision 0011). Reads `data/staged/observations.parquet`. |
| `bench_analytics.py` | Times the `analytics` functions on the real staged data: the snapshot at any date and the 12-month change history with NumPy and, when installed, Numba, and the rate moves (NumPy only), each checked against the v0.1.0 `bisect` method or a `Decimal` loop. |

`bench_snapshot.py` reads the unzipped bulk file at `resources/WS_CBPOL_csv_flat.csv` (gitignored).
Get it from <https://data.bis.org/bulkdownload> under *Central bank policy rates (CSV, flat)*. The
scripts that read `data/staged/observations.parquet` need `bis-prates fetch` and `bis-prates
transform` first. Run them from the repository root.

## Run

```bash
uv sync --all-groups --all-extras

uv run python diagnostics/bench_pyarrow_types.py

uv run python diagnostics/bench_snapshot.py prep    # writes bench_base.parquet to a gitignored scratch folder
uv run python diagnostics/bench_snapshot.py run searchsorted_np 1

uv run python diagnostics/bench_kernels.py     # Cython needs a C compiler
uv run python diagnostics/bench_analytics.py
uv run python diagnostics/check_record_figures.py
```

Approaches: `bisect_py`, `searchsorted_np`, `pandas_merge_asof`, `arrow_join_asof`, `duckdb_asof`. The
scale factor replicates the 49 real daily series under new codes. Run each approach in its own
process, so the memory figures are not mixed between approaches.

## Findings (2026-11-17)

Environment: 4 cores, 16 GB RAM, Python 3.12, pandas 3.1.1, pyarrow 26.0.1, DuckDB 2.0.0.

### pyarrow-backed dtypes (`bench_pyarrow_types.py`)

| Check | Result |
|---|---|
| `decimal128(12, 4)`: (4.10 − 3.85) × 100 | Exact (`25.0000`), returned as `Decimal`; in float it is `24.999999999999957` |
| Parquet round trip | Keeps `decimal128(12, 4)` and `date32`, a date in 1946 included |
| `merge_asof` with `date32[pyarrow]` join keys | `TypeError`. It works after converting the keys to `datetime64[ns]`. |
| Cast `"3.12345"` to scale 4 | Raises "would cause data loss" on pandas 3.1.1. **pandas 2.3.3 silently rounded it to `3.1235`.** |

The last row shows that library defaults are not a validation strategy. `transform` does not rely
on the cast: it parses each value exactly, and a value with more decimals than the configured
scale is kept with no value, marked invalid and reported as a `non_numeric_value` finding, never
rounded. A regression test in `tests/test_transform.py` pins this. The separate rule "more decimals
than the series' `DECIMALS`" (`excess_decimals`) is configured but not implemented yet.

### 12-month look-back (`bench_snapshot.py`)

The task is to find, for every series, the latest valid observation and the last valid
observation on or before the date 12 calendar months earlier. All five approaches return identical
results at every scale (same `digest`), after the pyarrow workaround below.

| Approach | 49 series, 0.64M rows | 490 series, 6.4M rows | 2,450 series, 32M rows | Extra peak memory at 50× |
|---|---|---|---|---|
| Python `bisect` per series | 1.2 s | 12.3 s | 64.8 s | 1.8 GB |
| **NumPy `searchsorted` per series, on the Arrow date buffers** | **0.07 s** | **0.6 s** | **3.9 s** | 1.8 GB |
| pandas `merge_asof` (decimal values) | 1.1 s | 12.2 s | 92.5 s | 2.7 GB |
| pyarrow `join_asof` (with workarounds) | 0.25 s | 2.6 s | 14.2 s | 1.8 GB |
| DuckDB `ASOF JOIN` | 0.18 s | 0.9 s | 6.4 s | 2.1 GB |

v0.1.0 shipped the Python `bisect` version, run only for the requested countries or areas. Since #13 the report
and the `analytics` module use NumPy `searchsorted` on fixed-point arrays (see
`docs/adr/0006-twelve-month-look-back.md` and `docs/adr/0013-fast-analytics-numpy-numba.md`).

**Decision (2026-11-17):** NumPy `searchsorted`, kept behind a stable function contract, with the
Python `bisect` version as the **test oracle** and DuckDB as the documented scaling path.

When to revisit the decision:

| Trigger | Change |
|---|---|
| About 10⁵ short series, or the per-series loop dominating the run time | Vectorise fully, with one `searchsorted` over a combined (series, date) key |
| In-memory data above about ¼ of RAM, or the data stored in a lakehouse | DuckDB, or the computation pushed to the engine where the data lives |
| High API request rate | Precompute snapshots per source release |

### pyarrow `join_asof` and dates before 1970 (`bench_pyarrow_types.py`)

`date32` counts days since 1970-01-01, so earlier dates are negative. With pyarrow 26.0.1, if the
right table holds **any** negative key, `join_asof` finds **no match** for the group and raises no
error. Many BIS series start in the 1940s and 1950s, so every prior rate came back empty. The
benchmark's cross-check exposed it.

Other limits of `join_asof` found in the same spike:

- decimal value columns are rejected
- a maximum look-back (`tolerance`) is mandatory and acts as a hard cutoff
- the right-hand key column is dropped from the result

The approach was rejected for these reasons. The benchmark's version uses workarounds for all of
them (shifted keys, a row index, a tolerance wider than the whole history), only so it can be
compared with the others.

## Findings (2026-11-19): pure Python vs Numba vs Cython (`bench_kernels.py`)

Environment: 4 cores, Python 3.12, numpy 2.5.5, Numba 0.68.1, Cython 3.3.1. Input: the real staged
data, 639,926 valid daily observations in 49 series. Best of 5 runs; two runs of the script.

| Version | One-off cost | Run | Speed-up |
|---|---|---|---|
| Pure Python | none | 330–490 ms | 1× |
| Numba | 1.0 s compile on first call, in every new process | 3.9–5.9 ms | ~55–125× |
| Cython | 8–11 s build (C compiler needed) | 2.9–5.5 ms | ~90–110× |

All three give identical results. Numba and Cython are equally fast here: the difference between
them is within run-to-run noise. They differ in what they cost the project:

- Numba is a decorator on the same Python loop: no build step, and the package stays pure Python.
  The compile cost is paid once per process; `cache=True` keeps the compiled code on disk.
- Cython needs a compiler and a build step, so the package would ship compiled wheels per platform
  and Python version, with a different build backend than hatchling and matching CI jobs.

**Update (2026-11-22).** The Cython kernel was rewritten at C level, and both versions changed
how they allocate the result:

- **Cython:** contiguous typed memoryviews (`[::1]`, so each access is a pointer offset with no
  stride), bounds, wraparound and initialisation checks off, the loop in a `nogil` function, and
  `-O3 -march=native`, the target Numba compiles for. Cython's annotation shows no Python call in
  the loop. `cimport numpy` and its buffer syntax would add nothing: the loop never calls NumPy.
- **Both versions:** the result is allocated with `np.empty`, and the loop writes each element
  once. Filling it first with `np.full` cost about a quarter of the Numba run (4.5 ms against 3.3
  ms).

Four runs on a loaded machine (load average 8 on 4 cores; Cython 3.3.1): Numba 5.0–6.0 ms, and
Cython 2.4–5.9 ms after a 13.5–17.7 s build. Cython was faster in three of the four runs. The
range is too wide to settle the comparison: it needs runs on an idle machine. The choice of Numba
does not rest on speed alone (above), so it stands.

## Findings (2026-11-20): the analytics kernels (`bench_analytics.py`)

Same environment, numpy 2.5.5, Numba 0.68.1. Input: the real staged data (49 series, 639,926 valid
observations). The data is laid out once (`rate_history`, 0.22–0.32 s): per series, dates as int32
day numbers and values as int64 scaled by 10⁶, so every change in basis points is an exact integer.
Three runs; the ranges are run-to-run noise.

| Task | v0.1.0 method | NumPy | Numba |
|---|---|---|---|
| Snapshot of all 49 countries or areas at one date (a date-slider step) | 0.7–1.0 s (`bisect` on Python lists, rebuilt every call) | 51–56 µs | 41 µs |
| One search for all 49 series (half of a slider step) | | 10–13 µs | 1.9 µs |
| 12-month change for every observation | 2.9 s (`Decimal` loop) | 89–96 ms | 68–76 ms |
| Every rate move with its size (33,110 moves) | | 1.0 ms | NumPy only |
| The report's snapshot, all 49 countries or areas (`compute_snapshots`) | 2.3 s | 0.21–0.23 s | |
| First call of the two compiled searches | | | 0.68 s compiling; 0.17 s from the disk cache |

All results are exactly those of the slower code they replace: 0 mismatches over 25 dates × 49
series, and over all 639,926 rows for the change history.

What the numbers say:

- Most of the gain comes from building the arrays once and answering each request from them, not
  from compiling: a slider step goes from about a second to about 50 µs with NumPy alone.
- Numba makes the search itself about 5× faster, but a slider step only about 1.3× faster: the
  rest of the step is the calendar arithmetic (`months_back`) and NumPy's own call overhead.
- `cache=True` cuts the first call from 0.68 s to 0.17 s in every process after the first.
- Numba stays an optional extra (`fast`); without it, the same functions run on NumPy.
