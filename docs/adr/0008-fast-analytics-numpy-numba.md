# 0008 Fast analytics: fixed-point arrays, NumPy kernels, Numba optional

- **Status:** Accepted
- **Decided:** 2026-11-19 (Numba over Cython), 2026-11-20 (the analytics module)
- **Supersedes:** the shipped search of [0007](0007-twelve-month-look-back.md). Its target-date
  rule and its "on or before" rule stay.

## Context

v0.1.0 finds the rate 12 months earlier with Python's `bisect`, one requested area at a time, after
building Python lists of dates and `Decimal` values. That takes about 1 second for five areas, which
is fine for a command. The web front end (M8) will recompute on every user action, such as moving
a date slider or switching areas, and needs answers in well under a millisecond. The same front end
needs two more calculations: the 12-month change for every observation, and every rate decision
with its size.

The benchmark behind [0007](0007-twelve-month-look-back.md) had already found NumPy `searchsorted`
the fastest search. A second trial (`diagnostics/bench_kernels.py`) compared pure Python, Numba and
Cython on one kernel.

## Decision

- **Prepare once, answer many times.** `analytics.rate_history` turns the staged observations into
  NumPy arrays once (about 0.2 s): the valid observations of all series end to end, dates as int32
  day numbers, and rates as int64 in units of 10⁻⁶ percent (the configured scale). A change in
  basis points is then an exact integer difference, so the arithmetic stays exact as
  [0006](0006-exact-decimal-rates.md) requires.
- **One search for all series.** Each date is packed with its series position into one int64
  (`series × 2³² + day`), which rises across the whole array, so one `np.searchsorted` call answers
  a question for every series at once.
- **Three functions on the arrays:** `snapshot_at` (latest, target date, prior and change for every
  series as of any date), `change_history` (the change of every observation) and `rate_moves`
  (every date on which a rate changed).
- **The report uses the same search.** `compute_snapshots` builds the arrays for the requested
  series and calls `snapshot_at`. The `bisect` method of v0.1.0 stays in the tests and the benchmark
  as the reference.
- **Numba is optional.** Each of the two searches has a NumPy version and a plain loop. With the
  `fast` extra (`numba`) installed, the loops are compiled with `cache=True` and used; without it,
  the NumPy versions run. The package keeps no compiled dependency. `rate_moves` needs no search
  and runs on NumPy only: a compiled loop was slower than the NumPy comparison (3.7 ms against
  1 ms).
- **Read-only arrays.** The arrays of a `RateHistory` cannot be written to, so one history can be
  shared by every request of a server.
- **`transform` casts plain numbers in one go.** Values that are plain numbers fitting the
  configured precision and scale are cast by Arrow at once. Any other value still goes through the
  exact `Decimal` parser, so the staged files do not change.

## How it was chosen

On the real data (49 series, 639,926 valid observations), from `diagnostics/bench_analytics.py`:

| Task | v0.1.0 method | NumPy | Numba |
|---|---|---|---|
| All 49 areas at one date (one slider step) | ~1 s | ~55 µs | ~41 µs |
| 12-month change for every observation | 2.9 s (`Decimal` loop) | ~90 ms | ~70 ms |
| Every rate move | — | ~1 ms | NumPy only |
| The report's snapshot, all 49 areas | 2.3 s | 0.2 s | |

All results are identical to the slower method they replace: 0 mismatches over 25 dates × 49
series for the snapshot and over all 639,926 rows for the change history. The real `out/` files are
identical byte for byte, and so are the staged files after `transform`.

Most of the gain comes from preparing the arrays once. Numba makes the search itself about 5×
faster, but a whole slider step only about 1.3×, because the calendar arithmetic and NumPy's call
overhead remain. The first call compiles in 0.68 s; with `cache=True`, later processes load the
compiled code in 0.17 s (`diagnostics/README.md`).

## Alternatives considered

| Alternative | Why not |
|---|---|
| Cython | As fast as Numba on these kernels, but needs a C compiler, compiled wheels per platform and Python version, a different build backend and matching CI jobs |
| Numba as a required dependency | The NumPy versions are already within the target; a compiled dependency in every install is not needed for that margin |
| Pure NumPy, no Numba at all | Kept as the default; Numba stays available for the extra margin a server may want |
| DuckDB `ASOF JOIN` | Fast, but a new engine for in-memory data of this size; still the path for data larger than memory ([0007](0007-twelve-month-look-back.md)) |
| Keep `bisect` in the report and use the arrays only for the front end | Two searches to keep in agreement; one search tested against one reference is simpler |
| Explicit Numba signatures (eager compilation) | They move the compilation from the first call to import time, so every command, `fetch` and `--help` included, would pay it; with `cache=True` the first call already loads from disk |
| `fastmath`, `boundscheck`, `inline` | No effect here: `fastmath` relaxes floating-point rules and the kernels are integer only; bounds checks are already off by default; `inline` inlines one Numba function into another, and the kernels call none |
| Pydantic models for the arrays | Pydantic can only check that a field is an array, not its dtype or shape, and validation would cost time on every slider step; these are results passed between our own functions, which the code base keeps as frozen dataclasses. The API turns them into its own response models |

## Consequences

- NumPy becomes a direct dependency (it was already installed through pandas).
- The searches have two versions that must agree; a test compares them, and runs the loops as
  plain Python when Numba is not installed.
- A server should call the searches once at start-up, so a compilation or cache load is not paid
  by the first user.

## Evidence

- `diagnostics/bench_analytics.py`, `diagnostics/bench_kernels.py` and their findings in
  `diagnostics/README.md`.
- `tests/test_analytics.py`: the search against the `bisect` method at every date of a fixture
  with a weekend target, 29 February, dates before 1970, negative rates, invalid rows, insufficient
  history and no valid data; the NumPy and loop kernels against each other; the change history and
  the rate moves against exact values.
- `tests/test_snapshot.py`: the report's snapshot, unchanged since v0.1.0, passes on the new search.
