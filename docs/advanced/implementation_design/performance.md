# Performance

The measured figures behind the performance choices, each with the date it was taken. All figures
are from `diagnostics/README.md` and the decision records; none was produced for this report. The
scripts that produce them are in `diagnostics/` (not part of the package: nothing in `src/`
imports from it) and can be rerun from the repository root.

## Reading the figures

- The machine is stated in `diagnostics/README.md`: 4 cores, 16 GB of RAM and Python 3.12 for the
  first benchmark, 4 cores and Python 3.12 for the second, and "the same environment" for the
  third. The kernel benchmark of 2026-11-19 was rerun on
  2026-11-22 on a loaded machine (load average 8 on 4 cores), and its author notes that the
  comparison between Numba and Cython needs runs on an idle machine.
- Ranges are run-to-run noise over the runs stated, not confidence intervals.
- The benchmarks use the real BIS file as it was on the date shown (49 daily series, 639,926 valid
  observations), or that data replicated under new codes for the scaling runs.

## The data

| Item | Size | Source |
|---|---|---|
| The download (ZIP) | 4.1 MB | [Data model](../../data-model.md#raw) |
| The CSV inside it | 477 MB | same |
| Rows in the CSV | 735,156 | same |
| The raw parquet file | 1.8 MB | [decision 0012](../../adr/0012-elt-raw-and-staged-layers.md) |
| Staged observations | 709,984 rows, of which 639,926 valid, in 49 daily series | [Data model](../../data-model.md#staged), release of 19 November 2026 |

## The 12-month look-back (2026-11-17)

Environment: 4 cores, 16 GB RAM, Python 3.12, pandas 3.1.1, pyarrow 26.0.1, DuckDB 2.0.0. The task
is to find, for every series, the latest valid observation and the last valid one on or before the
date 12 calendar months earlier. All five approaches return identical results at every scale.

| Approach | 49 series, 0.64M rows | 490 series, 6.4M rows | 2,450 series, 32M rows | Extra peak memory at 50× |
|---|---|---|---|---|
| Python `bisect` per series | 1.2 s | 12.3 s | 64.8 s | 1.8 GB |
| NumPy `searchsorted` per series, on the Arrow date buffers | 0.07 s | 0.6 s | 3.9 s | 1.8 GB |
| pandas `merge_asof` (decimal values) | 1.1 s | 12.2 s | 92.5 s | 2.7 GB |
| pyarrow `join_asof` (with workarounds) | 0.25 s | 2.6 s | 14.2 s | 1.8 GB |
| DuckDB `ASOF JOIN` | 0.18 s | 0.9 s | 6.4 s | 2.1 GB |

Decision of 2026-11-17: NumPy `searchsorted`, with `bisect` kept as the test oracle and DuckDB as
the documented scaling path ([decision 0006](../../adr/0006-twelve-month-look-back.md)). The
revisit triggers are in `diagnostics/README.md`: about 10^5 short series; in-memory data above about
a quarter of RAM; a high API request rate.

Update of 2026-11-22: `ASOF JOIN` is DuckDB's own extension, not standard SQL, and the look-back
does not need it. A standard query with two aggregates, timed on a loaded machine with DuckDB 2.0.1,
took 0.11 s, 0.46 s and 2.1 s at the three sizes, against 0.17 s, 0.86 s and 4.3 s for `ASOF JOIN`
and 0.07 s, 0.78 s and 4.5 s for the NumPy search in the same session. For one run over the whole
file, standard SQL is faster from 10× up, as it uses every core. NumPy stays for the cost per
request: once the arrays are built, a snapshot of all 49 series at one date takes 51 to 56 µs (see
the analytics kernels below), against about 56 ms on a warm DuckDB connection. The scaling path is
standard SQL in the engine where the data lives.

The same benchmark found that pyarrow 26.0.1 `join_asof` finds no match for a group whose right
table holds any date before 1970 and raises no error. Many BIS series start in the 1940s and 1950s,
so the approach was rejected on correctness before speed.

## Fixed-point kernels: Python, Numba, Cython (2026-11-19, updated 2026-11-22)

Input: the real staged data, 639,926 valid daily observations in 49 series; the 12-month change in
basis points for every valid observation, in exact integer arithmetic. Best of 5 runs, two runs of
the script.

| Version | One-off cost | Run | Speed-up |
|---|---|---|---|
| Pure Python | none | 330 to 490 ms | 1× |
| Numba | 1.0 s compile on first call, in every new process | 3.9 to 5.9 ms | about 55 to 125× |
| Cython | 8 to 11 s build (C compiler needed) | 2.9 to 5.5 ms | about 90 to 110× |

Update of 2026-11-22: after the Cython kernel was rewritten at C level and both versions stopped
pre-filling their result array, four runs on a loaded machine gave Numba 5.0 to 6.0 ms and Cython
2.4 to 5.9 ms after a 13.5 to 17.7 s build. The range is too wide to settle which is faster. The
choice of Numba does not rest on speed alone: it is a decorator on the same loop, with no build step and
no compiled wheel per platform ([decision 0013](../../adr/0013-fast-analytics-numpy-numba.md)).

## The analytics kernels (2026-11-20)

Input: the same staged data. The arrays are built once (`rate_history`, 0.22 to 0.32 s). Three
runs.

| Task | v0.1.0 method | NumPy | Numba |
|---|---|---|---|
| Snapshot of all 49 countries or areas at one date | 0.7 to 1.0 s | 51 to 56 µs | 41 µs |
| One search for all 49 series | - | 10 to 13 µs | 1.9 µs |
| 12-month change for every observation | 2.9 s (`Decimal` loop) | 89 to 96 ms | 68 to 76 ms |
| Every rate move with its size (33,110 moves) | - | 1.0 ms | NumPy only |
| The report's snapshot, all 49 countries or areas (`compute_snapshots`) | 2.3 s | 0.21 to 0.23 s | - |
| First call of the two compiled searches | - | - | 0.68 s compiling; 0.17 s from the disk cache |

The results equal those of the slower code: 0 mismatches over 25 dates × 49 series, and over all
639,926 rows of the change history. What the figures say, in the author's reading: most of the gain
comes from building the arrays once and answering each request from them, not from compiling; Numba
makes the search about 5× faster but a date-slider step only about 1.3× faster, because the
calendar arithmetic and NumPy's own call overhead remain; a compiled loop for `rate_moves` was
slower than the NumPy version (3.7 ms against 1 ms, decision 0013), so it stays on NumPy.

## The API

Recorded in [decision 0014](../../adr/0014-http-api.md) (decided 2026-11-21): the staged data is
read once in 0.35 s; a `summary` builds the analytics arrays for the requested series on each
request, in 67 ms for five codes and 220 ms for all 49. They are not cached; the decision leaves
that until a front end needs faster answers.

## What is not measured

The repository holds no figure for:

- the time or the peak memory of `transform`, which holds the 4.1 MB ZIP and the 477 MB CSV as
  bytes and then parses the CSV as text;
- the time of `fetch` (network bound), of the PNG chart, of the Plotly chart or of the page
  rendering;
- the memory of a running `serve`, or its throughput under concurrent requests;
- the speed of `BulkFileProvider.observations`, which casts the stored table to the configured
  schema on each call.

Nothing in the design makes a promise about these. Where a limit is in the code it is stated on the
page that owns it, for example that the download and the CSV are read whole into memory
([Security](security.md#the-source-download)).

## Reproducing

```bash
uv sync --all-groups --all-extras
uv run python diagnostics/bench_snapshot.py prep
uv run python diagnostics/bench_snapshot.py run searchsorted_np 1
uv run python diagnostics/bench_kernels.py
uv run python diagnostics/bench_analytics.py
```

`bench_snapshot.py` reads the unzipped bulk file at `resources/WS_CBPOL_csv_flat.csv`; the others
read `data/staged/observations.parquet`, so `bis-prates fetch` and `bis-prates transform` come
first. Run each approach of `bench_snapshot.py` in its own process, so the memory figures are not
mixed. The full instructions are in
[`diagnostics/README.md`](https://github.com/andrea-dm/policy-rate-monitor/blob/develop/diagnostics/README.md).
