# 0012 ELT, not ETL: load the source as it is, then stage it

- **Status:** Accepted; the raw layer is built, row lineage and release history are not yet
- **Decided:** 2026-11-19

## Context

The first plan was ETL: `transform` would read the ZIP, filter and clean on the way, and store only
the canonical data. A row dropped or a value resolved on the way would then leave no trace, and an
auditor asking "where does this number come from?" or "what did the source say before you cleaned
it?" would get no answer.

## Decision

- **Load first, then stage.** `transform` first stores the CSV exactly as loaded, every row and
  every column as text, in `raw/<file>.parquet`, next to the ZIP and its release record (URL,
  retrieval time, SHA-256). Only then does it build the staged layer: the selection (`FREQ = D`),
  the exact decimal values, the validity flag, the series metadata and the data-quality findings.
- **Nothing is deleted on the way in.** Selection, de-duplication, invalid values and resolved
  conflicts happen in staging, and each one is recorded as a finding
  ([0011](0011-data-quality-rules.md)). Columns the configuration does not map are loaded into the
  raw layer all the same; the configuration calls them `unmapped`, not "dropped".
- **The layers map onto the commands.** `fetch` extracts the source and keeps the ZIP; `transform`
  loads it into the raw layer and stages it; `report` reads only the staged layer, through the
  provider ([0008](0008-provider-interface.md)).

## What is not built yet

- **Row lineage.** A staged row does not yet carry a pointer to its raw row; the series key and
  date find it.
- **A history of releases.** A new download replaces the previous ZIP and raw file; only the latest
  release is kept. Re-staging an old release needs its ZIP to be kept, with its release record.

## Alternatives considered

| Alternative | Why not |
|---|---|
| ETL, as first planned | A dropped or resolved row leaves no trace |
| Keeping only the ZIP as the raw layer | Every question about the raw data means unzipping and parsing the CSV again |
| A database for the raw layer | A dependency and a server for 735,000 rows that a parquet file holds |

## Consequences

- Every staged value and every finding can be checked against the source as loaded.
- The raw parquet file takes disk space: 1.8 MB for the BIS file, less than its 4.1 MB ZIP.
- The first consequence in the configuration, during #5: `columns.dropped` was renamed
  `unmapped`, with a comment giving the reason.

## Evidence

- `src/policy_rate_monitor/transform.py` (`transform`: the raw copy, then staging) and
  `src/policy_rate_monitor/fetch.py` (the ZIP and its release record, replaced only by a download
  that passed its checks).
- `data/raw/` and `data/staged/` after a run.

## Updates

- 2026-11-22 (#34): `transform` also stages the release record of the ZIP it read, in
  `staged/release.json`, and the report reads the release from there. Every staged file and every
  output is written to a temporary file and renamed into place once all are written,
  `staged/release.json` last, so a failure while writing leaves the previous set as it was (with
  no release to stage, the old `release.json` is removed first, so that set's release is then
  unknown); the
  raw `raw/<zip>.parquet` is written in the same set, at the end of the run, so a run that fails
  leaves the raw and staged files of the last run as they were
  ([0017](0017-bad-input-failures-and-writes.md)).
