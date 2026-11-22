# 0005 Exact decimal rates end to end

- **Status:** Accepted
- **Decided:** 2026-11-17, refined 2026-11-18

## Context

The report's main figure is the 12-month change in basis points, `(latest − prior) × 100`. With
floating point, `(4.10 − 3.85) × 100 = 24.999999999999957`. Profiling the BIS file showed this kind
of noise for 6 of the 49 areas. Rounding at the end would hide it, but the result would then depend
on a rounding rule rather than on the data.

## Decision

- **Parse from text.** Values are read from the CSV as text and turned into `Decimal`, with the
  unit multiplier applied exactly (`value × 10^UNIT_MULT`). A value that is not a number, or has
  more digits than the configured precision and scale, becomes an invalid observation with a
  data-quality finding. It is never rounded to fit.
- **Store exactly.** Staged values are `decimal128(15, 6)` ([0001](0001-data-stack-pyarrow.md)).
- **Compute exactly.** The snapshot works on `Decimal` values, and the change is computed in decimal
  arithmetic.
- **Strict models.** The domain models (`Rate`, `BasisPoints`) accept only `Decimal` from Python
  callers, with at most 15 significant digits, which is what a JSON number carries exactly. A float
  reaching a rate is a bug, and the model reports it where it happens.
- **`DECIMALS` is display precision.** `summary.json` holds the exact values as JSON numbers;
  `report.md` formats each rate with its series' `DECIMALS`. Stored values are never rounded to it.
- **Floats only for drawing.** The chart is the one place where rates become floats.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Floats, rounded when written | Hides the error; the result depends on the rounding rule |
| Rates as JSON strings (pydantic's default for `Decimal`) | Harder for every consumer of `summary.json` and the API |
| Models that accept floats and convert them | A float has already lost exactness, so the conversion only hides the bug |
| Rounding to `DECIMALS` inside the model | Formatting in the domain model; the API would return rounded values |

## Consequences

- The headline figures match an independent calculation on the raw CSV exactly.
- `Decimal` arithmetic is slower than float. The faster analytics planned for the interactive
  front end (#13) keep exactness with integers scaled by 10^6, not with floats.
- Python callers must pass `Decimal`; JSON input still accepts numbers.

## Evidence

- `tests/test_snapshot.py::test_change_is_exact_in_basis_points`; the domain-model tests in
  `tests/core/`.
- The "pyarrow-backed dtypes" table in `diagnostics/README.md` (exact decimal arithmetic; the
  silent rounding of pandas 2.3.3).

## Updates

- 2026-11-20: the figure of 6 areas is reproduced by `diagnostics/check_record_figures.py`.
- 2026-11-20: the faster analytics are built, on integers scaled by 10^6
  ([0013](0013-fast-analytics-numpy-numba.md)).
- 2026-11-22 (#34): a value is read only as a plain ASCII number that converts to a `Decimal`
  exactly, with rounding trapped, so a value with more digits than `Decimal` keeps (28) is no longer
  rounded on the way in, and text such as `1_5` is not a number. The summary table pads a rate to
  its series' `DECIMALS` but never rounds it ([0017](0017-bad-input-failures-and-writes.md)).
