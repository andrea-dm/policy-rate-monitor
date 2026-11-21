# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `examples/http_api.ipynb`, which uses only the HTTP API: start `bis-prates serve`, then the
  notebook runs the jobs and reads the summary, a time series and the chart over HTTP.
- The HTTP API: `GET /ref-areas`, `/summary`, `/timeseries` and `/chart`, and `fetch`,
  `transform` and `report` as background jobs with a status endpoint, one at a time. Errors
  are one JSON body, `{"error": kind, "message": ...}`, with a status per kind. See
  docs/api.md and decision 0014 (#27).
- `bis-prates serve [--host] [--port] [--out]` runs the API; without the `web` extra it
  ends with a message naming it and exit code 2. Ctrl+C waits for the running job.
- The extras `interactive` (Plotly) and `web` (FastAPI, Starlette, uvicorn and `interactive`).
- `examples/policy_rates.ipynb` also draws the interactive Plotly chart, as `/chart` returns it, and
  uses the `service` steps instead of repeating them (#27).

### Changed

- The steps the command line and the API share are in the new `service` module, and the
  report's choice of charted series and valid rows is shared with the interactive chart
  (`charts`), so both draw the same lines. The commands behave as before.
- One name for a country or economic area, `ref_area`, everywhere. **Breaking:** the
  configuration key `area_aliases` is now `ref_area_aliases`, and the snapshot status
  `unknown_area` is now `unknown_ref_area` in `summary.csv`, `summary.json` and `report.md`. The
  report's table header and the messages say "country or area" (#27).

## [0.2.0] — 2026-11-21

Fast analytics, the `config` commands and options from environment variables, and the full
documentation site.

### Added

- `analytics`: the staged observations as fixed-point NumPy arrays, with `snapshot_at` (latest
  rate, rate 12 months earlier and change for every area as of any date), `change_history` (the
  12-month change of every observation) and `rate_moves` (every rate change and its size), exact
  in integer arithmetic (#13).
- The optional extra `fast`, which installs Numba to compile the analytics kernels; without it they
  run on NumPy with the same results (#13).
- Documentation pages: how each task of the brief is solved, with the ingestion and caching
  processes step by step; the architecture; the data model and assumptions; the CLI reference; the
  API reference generated from the docstrings; a glossary; a new home page.
- Decision records for the charting and report format, the web extension stack, the domain model,
  the config-driven design, the provider interface, the SDMX validation, the command line and its
  exit codes, the data-quality rules, and the raw and staged layers.
- Two example notebooks: `examples/fetch_and_transform.ipynb` downloads and stages the data, as
  `fetch` and `transform` do; `examples/policy_rates.ipynb` loads the staged data through the
  provider, computes the 12-month change and plots the rates, as `report` does.
- `diagnostics/check_record_figures.py`, which reproduces the figures about the data quoted in the
  decision records.
- Docstrings for every public function, method and constant, each module linked to its page.
- `bis-prates config validate`, which checks the configuration the other commands would load, and
  `bis-prates config schema`, which prints its JSON Schema (#7).
- `BIS_PRATES_CONFIG` and `BIS_PRATES_DATA_DIR`, read when `--config` or `--data-dir` is not
  given; a configuration that cannot be loaded is reported with where its path came from (#7).
- `bis-prates --version` (#7).
- Each GitHub release carries the documentation site as a ZIP that opens from the disk,
  `policy-rate-monitor-docs-<tag>.zip`.

### Changed

- The decision records are renumbered, once, in the order the decisions were taken. Links to the
  old file names no longer resolve: `0005-data-stack-pyarrow.md` is now `0001-…`,
  `0006-exact-decimal-rates.md` is `0005-…`, and `0007-twelve-month-look-back.md` is `0006-…`.
- The report's snapshot uses the `analytics` search: about 0.2 s for all 49 areas instead of
  2.3 s, with identical results (#13).
- Installing the package now also installs NumPy (2.5.5 or later), which `analytics` needs (#13).
- `transform` casts plain numbers in one go and parses any other value exactly as before; the
  staged files are unchanged (#13).
- `SnapshotArgs` no longer takes `ref_area_column`: the snapshot finds an area's rows by its series
  key (#13).
- The schema is regenerated with `bis-prates config schema`; the
  `python -m policy_rate_monitor.config.schema` entry is removed (#7).
- Exit code 8 now says "cannot read or write a file", since it covers any file error, not only the
  outputs.
- The checks run in one order everywhere, and stop at the first failure: tach, ruff check, ruff
  format, deptry, pyright, pytest, the docs build. The commit hooks run tach before ruff, the push
  hooks deptry before pyright, and in CI each check job starts only when the one before passed.
- The message for exit code 1 says "no requested area has a complete result (status ok)", as the
  docs do.

### Fixed

- `transform` finds the CSV in the ZIP whatever the case of its extension, as `fetch` already did.
- Docstrings and pages that did not match the code: requested codes are kept as typed, not
  upper-cased; `version_id` is never set for the bulk download; the attribution is shown in the
  report only; an unexpected error's traceback is always logged.

## [0.1.0] — 2026-11-20

The first release: the `bis-prates` command line, from the BIS bulk download to the report.

### Added

- `bis-prates fetch`: finds the "Central bank policy rates (CSV, flat)" ZIP on the BIS
  bulk-download page (with a configured fallback URL), downloads it with timeouts and a bounded
  number of attempts, checks that it is a ZIP holding exactly one CSV, and records its URL,
  retrieval time and SHA-256. A copy younger than 24 hours is reused; a failed download never
  replaces it; with the network down, the cached copy is used with a warning (#10).
- `bis-prates transform`: stores the CSV as loaded, then stages the daily series as exact
  decimals with the unit multiplier applied and a validity flag per observation, the series
  metadata, and the data-quality findings (unparseable dates or numbers, exact and conflicting
  duplicates, unexpected attribute values) (#10).
- `bis-prates report`: per requested area, the latest valid rate, the most recent valid rate on
  or before the same calendar date 12 months earlier, and the change in basis points, computed
  exactly; statuses for an unknown area, no valid data and insufficient history, and flags for a
  stale latest or prior rate. Writes `summary.csv`, `summary.json`, `policy_rates.png` and
  `report.md` (#11). A requested code the data does not have gets a warning listing the codes it
  has, with their names.
- One exit code per kind of failure (#10).
- `DataProvider`, the interface through which the CLI reads data, with the canonical observations
  schema built from the configuration, `BulkFileProvider` over the bulk-download file,
  `InMemoryProvider` for tests, and contract tests every provider passes (#6).
- The dataset configuration `configs/bis_cbpol.json`, its JSON Schema, and the configuration
  models that validate it when it is loaded (#5).
- The domain models and typed errors (#2).
- The outputs of a real run in `out/`, and the README with installation, usage, the data model,
  assumptions, known issues of the BIS data, limitations and the AI usage note.
- `diagnostics/`: the spikes and benchmarks behind the technical choices, including a comparison
  of pure Python, Numba and Cython on one analytical kernel.
- CI: ruff, pyright (strict), deptry, tach, the docs build, dependency audits and the package
  build on Python 3.12, and pytest with the docstring examples on Python 3.12, 3.13 and 3.14.

[Unreleased]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/andrea-dm/policy-rate-monitor/releases/tag/v0.1.0
