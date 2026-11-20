# 0006 The 12-month look-back: binary search per area

- **Status:** Accepted. The search is superseded by [0013](0013-fast-analytics-numpy-numba.md);
  the target-date rule and the "on or before" rule stay.
- **Decided:** 2026-11-17 (method and benchmark), 2026-11-19 (shipped version)

## Context

For each requested area the report needs the latest valid rate and the rate 12 months earlier:
the most recent valid observation on or before the same calendar date 12 months before the latest
date. The data makes this less simple than it sounds:

- areas end on different dates, so there is no common "as of" date;
- series cover weekends and holidays differently, so the target date often has no observation;
- histories go back to the 1940s, so dates before 1970 are ordinary values.

## Decision

- **Target date:** the same calendar date `lookback_months` earlier (12 by default), clamped to the
  end of the month, so 29 February looks back to 28 February (`months_before`).
- **Prior rate:** a binary search (`bisect_right`) on the area's valid dates, sorted, for the last
  date on or before the target date.
- **Pure function:** `compute_snapshots` does no I/O. It relies on the provider contract (at most
  one row per series and date, sorted by date), which every provider is tested against.
- **Shipped version:** Python's `bisect`, run only for the requested areas.
- **Deferred:** NumPy `searchsorted`, the faster search chosen by the benchmark, is deferred to
  #13, which builds the fast analytics for the interactive front end.

## How it was chosen

Five methods were benchmarked on all 49 series of the real file, and at 10× and 50× its size. All
gave identical results.

| Method | 49 series, 0.64M rows | 2,450 series, 32M rows |
|---|---|---|
| Python `bisect` per series | 1.2 s | 64.8 s |
| NumPy `searchsorted` per series | 0.07 s | 3.9 s |
| pandas `merge_asof` | 1.1 s | 92.5 s |
| pyarrow `join_asof` (with workarounds) | 0.25 s | 14.2 s |
| DuckDB `ASOF JOIN` | 0.18 s | 6.4 s |

NumPy `searchsorted` was the fastest and was recorded as the target engine, with `bisect` as the
reference implementation for tests. The first release ships the `bisect` version: the report
computes only the areas the user asks for, and the simplest correct code came first. On the real
data it takes 1.1 s for five areas and 2.3 s for all 49, which is fine for a command-line run.
The fast version, on integer arrays with NumPy and optionally Numba, is planned for the
interactive front end, where the calculation runs on every user action (#13).

## Alternatives considered

| Alternative | Why not |
|---|---|
| pandas `merge_asof` | Rejects Arrow date keys; slowest at scale |
| pyarrow `join_asof` | Finds no match when any key is before 1970, with no error; rejects decimal values; needs a hard maximum look-back |
| DuckDB `ASOF JOIN` | Fast, but a new dependency for a five-area report; kept as the path for data larger than memory |
| 365 days instead of calendar months | Wrong across leap years |
| Business days | Series cover weekends differently, so "on or before" is the only rule that fits all of them |

## When to revisit

| Trigger | Change |
|---|---|
| Interactive use: the calculation runs on every user action | NumPy or Numba over integer arrays (#13) |
| About 10⁵ short series, or the per-series loop dominating the run time | One `searchsorted` over a combined (series, date) key |
| Data above about a quarter of RAM, or stored in a lakehouse | DuckDB, or the engine where the data lives |

## Evidence

- `diagnostics/bench_snapshot.py`, `diagnostics/repro_pyarrow_join_asof_pre1970.py` and the
  look-back tables in `diagnostics/README.md`.
- `tests/test_snapshot.py`: a target date on a weekend, 29 February, negative rates, a different
  latest date per area, insufficient history.
