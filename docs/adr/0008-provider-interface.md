# 0008 A source-agnostic `DataProvider` that returns canonical data

- **Status:** Accepted
- **Decided:** 2026-11-17; built after the pipeline, on 2026-11-20 (#6)

## Context

Today the data comes from one place: the BIS bulk-download file. Tomorrow it could come from the
BIS SDMX data API, a query engine over a lakehouse (DuckDB, Spark), or another REST API. The
calculation, the report and the planned HTTP API should not care which, and should not change
when a source is added.

## Decision

- **One interface, `DataProvider`** (`providers/base.py`), an abstract base class with two kinds of
  methods:
  - **lifecycle** methods, which change state and map to the CLI commands: `fetch()` gets the
    source data or makes sure a recent copy is at hand, and returns its `SourceRelease`;
    `transform()` builds the canonical data;
  - **query** methods, which only read and are the only way the rest of the package reads data:
    `list_series()`, `series_metadata(key)`, `observations(series_keys=None)`,
    `data_quality_issues()` and `source_release()`.
- **Canonical data out.** Every provider returns the same thing whatever its source: an Arrow
  table in one schema built from the configuration (`observation_schema`), the series metadata, the
  data-quality findings and the release record, as domain models
  ([0004](0004-domain-model.md)).
- **A contract every provider passes.** `observations` has at most one row per series and date,
  sorted by date within each series, with the validity flag set and a value on every valid row.
  The snapshot's binary search relies on it ([0006](0006-twelve-month-look-back.md)). One shared
  test suite runs against every provider.
- **Settings in the constructor.** What is fixed for a provider's lifetime (the configuration, the
  data directory, how to download) is passed when it is created, not to `fetch()`. A `fetch(url)`
  would mean nothing to an in-memory or a query-engine provider, and would leak a bulk-download
  detail into the shared interface.
- **Two providers today.** `BulkFileProvider` over the bulk-download file, and `InMemoryProvider`
  for tests and examples. The planned SDMX work validates against the publisher's metadata
  ([0009](0009-sdmx-validation.md)); an SDMX *data* provider is not planned, though the research
  showed it is feasible and could reuse the bulk reader.

## How it evolved

The interface was designed on 2026-11-17. The review of the milestone plan added the two query
methods for findings and provenance, so that the report would not read the bulk provider's files,
and made the sorted, one-row-per-date contract explicit. On 2026-11-19, to deliver a working tool by
the deadline, the interface was postponed. It was built on 2026-11-20, once the pipeline worked,
with the CLI reading through it.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Providers returning raw source rows | Every consumer would still depend on the source's format |
| Lifecycle methods only, consumers reading the stored files | Couples every consumer to one provider's storage |
| A bulk-only design | Simpler now, but every new source would ripple through transform, analytics and report |
| Building an SDMX data provider up front | The interface would be guessed before a second real implementation exists |
| `typing.Protocol` instead of an abstract base class | A missing method would only be a type error; with an ABC, a provider missing a method cannot be created |
| The snapshot sorting and de-duplicating its own input | Hides a provider's defects |

## Consequences

- Adding a source means writing one class and passing the contract suite.
- The CLI converts the Arrow table to pandas once, for the snapshot and the report.
- The interface has seven methods to implement, even for a provider that is only a test stand-in.

## Evidence

- `src/policy_rate_monitor/providers/` (`base.py`, `schema.py`, `memory.py`) and
  `src/policy_rate_monitor/bulk.py`.
- `tests/providers/contract.py`: the shared suite (`test_observations_have_the_canonical_schema`,
  `test_one_row_per_series_and_date`, `test_dates_are_sorted_within_each_series`,
  `test_validity_is_set_and_valid_rows_have_a_value`, …), run by `tests/providers/test_bulk.py` and
  `tests/providers/test_memory.py`; `tests/providers/test_contract.py` checks that each check fails on
  a table that breaks it, and that a provider missing a method cannot be created.

## Updates

- 2026-11-22: the table is converted to pandas once by `service.load_staged`, for the CLI and the
  HTTP API ([0014](0014-http-api.md)). Since #34, `transform()` returns the counts of the run
  (`TransformSummary`), and `source_release()` reads the release that was staged
  (`staged/release.json`), not the record next to the cached ZIP
  ([0017](0017-bad-input-failures-and-writes.md)).
