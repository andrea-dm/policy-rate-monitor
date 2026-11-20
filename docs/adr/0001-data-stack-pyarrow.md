# 0001 Arrow types and parquet for the data; Arrow tables at the provider interface

- **Status:** Accepted
- **Decided:** 2026-11-17 (data types and storage), 2026-11-20 (provider interface)

## Context

The pipeline holds about 710,000 daily observations of 49 series. Three properties of the data
drive the choice of types:

- **Rates must be exact.** The 12-month change is reported in basis points, and floating point gets
  it wrong: `(4.10 − 3.85) × 100 = 24.999999999999957`.
- **Dates are calendar dates**, with no time or time zone, and many series start in the 1940s.
- **The data moves between stages and, later, between systems.** `transform` writes it, the
  snapshot and the report read it, and the planned HTTP API and other providers (a query engine,
  another API) will exchange it.

## Decision

- Rates are stored as `decimal128(precision, scale)`, with the precision and scale declared in the
  configuration (`15, 6` for the BIS dataset), and dates as `date32`. pandas 3 holds them with its
  pyarrow-backed types (`dtype_backend="pyarrow"`, `ArrowDtype`), so values come out as Python
  `Decimal` and `date` objects.
- The raw and staged layers are parquet files.
- `DataProvider.observations()` returns a `pyarrow.Table` in one canonical schema, built from the
  configuration by `observation_schema`: names, types, column order and nullability. The CLI
  converts it to pandas once, keeping the Arrow types, for the snapshot and the report.

## Reasons

- **Exact decimals with a declared precision and scale.** Arrow has a real decimal type; NumPy has
  none.
- **A schema that can be checked.** An Arrow table carries its schema, including which columns may
  be empty. The provider contract checks it in one line (`table.schema.equals(schema)`). A pandas
  DataFrame has types but no nullability and no declared schema.
- **Plain dates, before 1970 included.** `date32` is a day count, so a date in 1946 is an ordinary
  value.
- **One format from disk to memory.** Parquet is Arrow's file format: types survive the round trip,
  with no conversion.
- **It is what data platforms use.** Parquet and Arrow are the formats of lakehouse platforms
  (Iceberg, Spark) and query engines (DuckDB, Polars). A future provider over one of them returns
  its native type, with no conversion to pandas.

## Alternatives considered

| Alternative | Why not |
|---|---|
| NumPy-backed `float64` | Binary rounding, so basis points are not exact; weaker parquet types |
| NumPy or pandas `object` arrays of `Decimal` | Slow, with no declared type and no schema |
| A pandas DataFrame at the provider interface | No nullability and no schema to check; every provider would have to convert to pandas |
| polars | Less familiar, and the look-back needs no join ([0006](0006-twelve-month-look-back.md)) |
| One pydantic model per observation | Slow and pointless at 710,000 rows; models are kept for the domain objects |

## Consequences

- Values stay exact from the CSV to the summary files ([0005](0005-exact-decimal-rates.md)).
- pyarrow ships no type information. `pyarrow-stubs` is in the `lint` group; three lines keep a
  targeted `# pyright: ignore` where the stubs leave a parameter untyped (`is_in`, `sort_by`,
  `to_pandas`).
- The CLI converts the table to pandas once, with `types_mapper=pd.ArrowDtype`.
- Library behaviour around these types must be pinned by tests, not assumed. The spike found that
  pandas 2.3.3 silently rounded a decimal cast that pandas 3.1.1 rejects (hence `pandas>=3.1.1`),
  that `merge_asof` rejects Arrow date keys, and that pyarrow's `join_asof` finds no match when a
  key is before 1970.

## Evidence

- `diagnostics/spike_pyarrow_dtypes.py` and the "pyarrow-backed dtypes" table in
  `diagnostics/README.md`.
- `tests/providers/`: the contract suite, which checks the canonical schema on both providers.

## Updates

- 2026-11-20: `analytics` adds a fourth targeted `# pyright: ignore`, where the stubs leave the
  decimal type's scale untyped ([0013](0013-fast-analytics-numpy-numba.md)).
