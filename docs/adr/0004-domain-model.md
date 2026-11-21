# 0004 The domain model: frozen pydantic models with checked invariants, and their vocabulary

- **Status:** Accepted
- **Decided:** 2026-11-17; hardened on 2026-11-18

## Context

The layers exchange a few kinds of objects: the description of a series, the result of the
12-month calculation for one requested area, a data-quality finding, and the record of a source
file. The same objects end up in `summary.json` and, with the web extension, in the HTTP API. Bulk
data, about 710,000 observations, is a different kind of thing: it is processed by column, not one
object at a time.

## Decision

- **Pydantic models for the domain, columnar data for the observations.** `core/models.py` defines
  `SeriesMetadata`, `DataSnapshot` (with `SnapshotStatus` and `SnapshotFlag`), `DataQualityIssue`
  (with `DataQualitySeverity`) and `SourceRelease`. The observations stay in Arrow tables and pandas
  DataFrames ([0001](0001-data-stack-pyarrow.md)).
- **Frozen, strict, no extra fields.** A model cannot be changed after it is built, and an unknown
  field is an error.
- **Invariants checked when an object is built.** A `DataSnapshot` with status `OK` has every value;
  its dates are ordered (`prior_date ≤ target_date ≤ latest_date`); its change equals
  `(latest − prior) × 100` exactly. A snapshot for an unknown area carries no series, values or
  flags. A calculation bug therefore fails where it happens, not in the report.
- **Status and flags kept apart.** A snapshot has exactly one `status` (`OK`, `UNKNOWN_REF_AREA`,
  `NO_VALID_DATA`, `INSUFFICIENT_HISTORY`) and zero or more `flags` (`STALE_LATEST`,
  `STALE_PRIOR`). A flag never hides a value. Flags are a sorted tuple, so the output files are
  identical from run to run.
- **The snapshot holds its series metadata.** `summary.csv` writes the selected metadata fields
  as columns; `summary.json` nests the full metadata. This covers both options of brief §3.2.
- **Names in full, and one meaning per word.**
  - `SeriesMetadata`, `DataSnapshot`, `DataQualityIssue`, not abbreviations.
  - *Snapshot* is the calculation of brief §1.3 for one area; *summary* is the output built from
    the snapshots (`summary.csv`, `summary.json`); *release* is one version of a source file
    (`SourceRelease`). "Snapshot" is never used for a file version.
  - `ref_area`, the SDMX name, rather than `country`: the euro area (`XM`) is not a country.
    `requested_ref_area` keeps the code as the user typed it (`EA`), next to the code used
    (`XM`). The CLI option stays `--countries`, the word the brief uses.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Dataclasses | No built-in checks of invariants; the HTTP API would need a second set of models |
| One model per observation | Slow and pointless at 710,000 rows |
| One combined status enum (`OK_STALE_LATEST`, …) | Grows with every combination of flags |
| Copying metadata fields into the snapshot | Duplicates data that can drift apart |
| `SnapshotRow`, `AreaSnapshot`, `SeriesMeta` | "Row" is table vocabulary; "area" is too narrow for a generic snapshot; abbreviations read badly |
| `country` | Wrong for the euro area |

## Consequences

- The HTTP API can return the same models, so `summary.json` and the API share one definition.
- Building a model costs some time; that is why observations are never models.
- `model_copy(update=...)` skips validation, as pydantic designs it; the module docstring says how
  to derive a changed object safely.

## Evidence

- `src/policy_rate_monitor/core/models.py`.
- `tests/core/test_models.py`: `test_ok_snapshot_requires_every_value`,
  `test_ok_snapshot_rejects_unordered_dates`,
  `test_ok_snapshot_rejects_a_change_inconsistent_with_the_rates`, `test_models_are_frozen`,
  `test_every_model_round_trips_through_its_own_json`, `test_flags_are_sorted_and_deduplicated`.

## Updates

- 2026-11-21 (#27): `ref_area` is the one name of the concept everywhere, not only in the model
  fields. Before the HTTP API froze them, the last public names that said "area" were renamed:
  the configuration key `area_aliases` is now `ref_area_aliases`, and the status `unknown_area`
  is now `unknown_ref_area`. Private names in the code and the tests follow, and prose says
  "country or economic area" ("country or area" when short), as the brief does. A test
  (`tests/test_vocabulary.py`) fails on any name in `src/`, `tests/` or the configuration files
  that contains "area" outside `ref_area`, and on the bare word in docstrings, comments, messages
  and the docs. Not taken: keeping "area" as a defined short form in prose, which reads more
  lightly but leaves two names for one concept and let the old ones slip back.
