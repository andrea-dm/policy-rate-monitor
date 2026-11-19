# diagnostics

Exploratory analysis, spikes and benchmarks behind the design decisions. This is not part of the
package: nothing in `src/` imports from here.

The scripts follow the repository's ruff rules, with `print()` allowed because printing is how they
report findings. pyright, tach and deptry do not cover this folder. The scripts depend on pyarrow and
DuckDB, which are not installed by `uv sync`; that is why the commands below use `uv run --with`.

| File | Purpose |
|---|---|
| `eda_cbpol.ipynb` | EDA of the BIS policy-rate bulk file (`WS_CBPOL`, CSV flat): structure, coding, missing values, duplicates, metadata, calendar density, recency, a prototype of the 12-month look-back, and the design implications (§17). **Gitignored** while in progress. |
| `spike_pyarrow_dtypes.py` | How pandas 3 behaves with pyarrow-backed `decimal128` and `date32` columns. Evidence for the pyarrow backend. |
| `bench_snapshot.py` | Benchmark of five ways to compute the 12-month look-back for every series, at 1×, 10× and 50× the real data. Evidence for the look-back engine. |
| `repro_pyarrow_join_asof_pre1970.py` | Minimal reproduction of a pyarrow `join_asof` bug: no match when any key is before 1970. |
| `bench_kernels.py` | Trial of pure Python, Numba and Cython on one kernel: the 12-month change in basis points for every valid observation of every series, in exact integer arithmetic. Reads `data/staged/observations.parquet` (written by `bis-prates transform`). |

All of them read the unzipped bulk file at `resources/WS_CBPOL_csv_flat.csv` (gitignored). Get it
from <https://data.bis.org/bulkdownload> under *Central bank policy rates (CSV, flat)*. Each one finds
the repository root by itself. Run them from the repository root.

## Run

```bash
uv sync --all-groups
uv run jupyter lab diagnostics/eda_cbpol.ipynb

uv run --with pyarrow python diagnostics/spike_pyarrow_dtypes.py
uv run --with pyarrow python diagnostics/repro_pyarrow_join_asof_pre1970.py

# duckdb is used only by this benchmark and is not a project dependency
uv run --with pyarrow --with duckdb python diagnostics/bench_snapshot.py prep    # writes tmp/bench_base.parquet
uv run --with pyarrow --with duckdb python diagnostics/bench_snapshot.py run searchsorted_np 1

# numba and cython are used only by this trial; a version that is not installed is skipped
uv run --with numba --with cython python diagnostics/bench_kernels.py
```

Approaches: `bisect_py`, `searchsorted_np`, `pandas_merge_asof`, `arrow_join_asof`, `duckdb_asof`. The
scale factor replicates the 49 real daily series under new codes. Run each approach in its own
process, so the memory figures are not mixed between approaches.

## Findings (2026-11-17)

Environment: 4 cores, 16 GB RAM, Python 3.12, pandas 3.1.1, pyarrow 26.0.1, DuckDB 2.0.0.

### pyarrow-backed dtypes (`spike_pyarrow_dtypes.py`)

| Check | Result |
|---|---|
| `decimal128(12, 4)`: groupby, subtraction, ×100 | Exact (`3.60 − 3.75 = −15.0000`) |
| Values returned as Python objects | `Decimal`, so there is no float step |
| Parquet round trip | Keeps `decimal128(12, 4)` and `date32` |
| `DateOffset(months=12)` on `date32[pyarrow]` | Works on pandas 3.1.1. It failed on pandas 2.3.3. |
| `merge_asof` with `date32[pyarrow]` join keys | `TypeError`. It works after converting the keys to `datetime64[ns]`. |
| Cast `"abc"` to decimal | Raises for the whole column, so values are validated before casting |
| Cast `"3.12345"` to scale 4 | Raises "would cause data loss" on pandas 3.1.1. **pandas 2.3.3 silently rounded it to `3.1235`.** |

The last row shows that library defaults are not a validation strategy. "More decimals than the
series' `DECIMALS`" is an explicit data-quality rule, and a regression test fixes the behaviour.

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

**Decision:** NumPy `searchsorted`, kept behind a stable function contract. The Python `bisect`
version is kept as the **test oracle**, and DuckDB is the documented scaling path.

When to revisit the decision:

| Trigger | Change |
|---|---|
| About 10⁵ short series, or the per-series loop dominating the run time | Vectorise fully, with one `searchsorted` over a combined (series, date) key |
| In-memory data above about ¼ of RAM, or the data stored in a lakehouse | DuckDB, or the computation pushed to the engine where the data lives |
| High API request rate | Precompute snapshots per source release |

### pyarrow `join_asof` and dates before 1970 (`repro_pyarrow_join_asof_pre1970.py`)

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
