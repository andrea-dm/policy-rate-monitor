# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-11-22

The HTTP API with `bis-prates serve`, an HTML report rendered from templates, charts that
can be read with a colour vision deficiency, and stricter handling of bad input and failures.

### Added

- Both charts, the PNG and the Plotly one (`/chart` and `report.html`), can be read with a colour
  vision deficiency: the colours of matplotlib's `tableau-colorblind10` that reach 3:1 on white,
  a dash pattern from the fifth line on (the first 24 lines all differ), each line named at its
  end, and a text alternative in `report.md`, `report.html`, the PNG's metadata and the figure's
  `layout.meta.description`. Both charts draw the n-th line the same way, and say "No data to
  plot" when empty. The Plotly hover shows the rate with the series' decimals. See decision 0016
  (#29).
- `bis-prates report` and the API's report job also write `out/report.html`: the same report as
  `report.md`, with the interactive chart when the `interactive` extra is installed and the PNG
  when it is not. The page is one file that works offline (Plotly's JavaScript is inside it, about
  5 MB) and follows WCAG 2.2 level AA. See decision 0015 (#12).
- Both report pages show an emoji beside each status, flag and finding severity (✅ ok, ⚠️ a
  warning, ⏳ a stale rate, and so on); the name is always written too (#12).
- `examples/http_api.ipynb`, which uses only the HTTP API: start `bis-prates serve`, then the
  notebook runs the jobs and reads the summary, a time series and the chart over HTTP (#30).
- The HTTP API: `GET /ref-areas`, `/summary`, `/timeseries` and `/chart`, and `fetch`,
  `transform` and `report` as background jobs with a status endpoint, one at a time. Errors
  are one JSON body, `{"error": kind, "message": ...}`, with a status per kind. See
  docs/api.md and decision 0014 (#27).
- `bis-prates serve [--host] [--port] [--out]` runs the API; without the `web` extra it
  ends with a message naming it and exit code 2. Ctrl+C waits for the running job (#27).
- The extras `interactive` (Plotly) and `web` (FastAPI, Starlette, uvicorn and `interactive`)
  (#27).
- `examples/policy_rates.ipynb` also draws the interactive Plotly chart, as `/chart` returns it, and
  uses the `service` steps instead of repeating them (#27).
- `transform` also writes `data/staged/release.json`, the release record of the ZIP it staged; the
  report and the API read the source release from there (#34).
- Decision 0017, on how bad input, failures and writes are handled, and a section on the HTTP API
  in the How it works page (#34).
- A Data quality page (what is checked, what a finding means, what to do about it) and an
  Advanced section in the documentation: the implementation design, the operations (inputs,
  outputs, call chains, CI/CD, releases, runbooks) and the decision records (#34).

### Changed

- The charts are one subpackage, `chart`: `lines` (`charted_series`, `valid_rows`), `style`,
  `static` (the PNG) and `interactive` (`rate_figure`). The report is another, `report`:
  `export` (`Report`, `ReportArgs`), `summary` and `pages`, with the templates in
  `report/templates/`. `Report` and `ReportArgs` are still imported from
  `policy_rate_monitor.report` (#29).
- `report.md` is rendered from a Jinja template, like `report.html`, from the same values; its
  text is the same as before, apart from the emoji. `jinja2` is a new dependency of the base
  install (#12).
- The steps the command line and the API share are in the new `service` module, and the
  report's choice of charted series and valid rows is shared with the interactive chart
  (`chart.lines`), so both draw the same lines. The commands behave as before (#27).
- One name for a country or economic area, `ref_area`, everywhere. **Breaking:** the
  configuration key `area_aliases` is now `ref_area_aliases`, and the snapshot status
  `unknown_area` is now `unknown_ref_area` in `summary.csv`, `summary.json` and `report.md`. In
  Python, `DatasetConfig.area_aliases` and `SnapshotArgs.area_aliases` are now
  `ref_area_aliases`, the type `config.models.AreaAliases` is now `RefAreaAliases`, and
  `SnapshotStatus.UNKNOWN_AREA` is now `SnapshotStatus.UNKNOWN_REF_AREA`. The report's table
  header and the messages say "country or area" (#27).
- **Breaking:** loading the configuration rejects a `keep` of `conflicting_duplicate` other than
  `first` or `last`, and an `unexpected_value` rule whose `params.expected` is missing, is not an
  object of codes, or names a column the configuration does not have (in `structure` or in
  `columns.unmapped`), with exit code 4, also from `bis-prates config validate`, whether the rule
  is on or off (#34).
- **Breaking:** `bis-prates report` and the API refuse a requested code that does not match the
  configuration's code pattern (letters, digits and `_ @ $ -`), with exit code 2 or `422` (#34).
- **Breaking:** `DataProvider.transform()` returns the counts of the run (`TransformSummary`),
  which `bis-prates transform` prints, and `source_release()` returns the release that was
  staged, `None` when nothing staged records one. `MissingInputError` takes the step to run
  (`step`); the command line words it as `bis-prates <step>`, the API as `POST /jobs/<step>`
  (#34).
- **Breaking:** `fetch` retries only a temporary failure (a connection error, a timeout, a 5xx,
  408 or 429), then ends with exit code 6; any other answer but 200, a 404 say, ends at once with
  exit code 7. Up to five redirects are followed (#34).
- Every staged file and output is written to a temporary file, then renamed into place once all
  are written, `staged/release.json` last: a failure while writing leaves the previous files as
  they were. A reader in another process can still pair files of two runs while the renames run,
  see #35 (#34).
- Run `bis-prates transform` once after upgrading: data staged by an earlier version has no
  `staged/release.json`, so its release shows as unknown until then (#34).
- The summary table in `report.md` and `report.html` pads a rate to its series' decimals and
  never rounds it, so the rates of a row add up to its change (#34).
- The API keeps the last 100 finished jobs (#34).
- In Python, `report.summary.write_csv(snapshots, fields, path)` is now
  `report.summary.summary_csv(snapshots, fields)`, which returns the text; `report.pages.ICONS`
  is a read-only mapping; and the new `chart.lines.chart_lines` gives the lines both charts draw
  (#34).
- `--version`, `fetch` and `transform` no longer import matplotlib and Jinja (pandas and pyarrow
  are still imported by every command), and the bulk provider reads `series.json` again only when
  the file changes (#34).

### Fixed

- After a `fetch` without a `transform`, the report named the new release next to the old
  figures; it now names the release that was staged (#34).
- `conflicting_duplicate.keep` applies with the rule switched off too, and the report always says
  which of two conflicting rows is kept (#34).
- A value is read only as a plain ASCII number that converts exactly: one with more than 28
  significant digits is no longer rounded, and text such as `1_5`, or a number padded with a
  non-breaking space, is no longer read as a number; a date in digits of another script is not
  read as a date (#34).
- A blank or non-whole `UNIT_MULT` no longer stops `transform`: its rows are kept as invalid,
  with a `non_numeric_value` finding. A bad `DECIMALS` leaves the series' decimals unknown, with
  a warning in the log (#34).
- A value that is not a number is reported once, as `non_numeric_value`, not also as
  `status_value_mismatch` (#34).
- A corrupt or encrypted ZIP, a CSV that cannot be parsed or decoded or has no row of the
  configured selection, and a series row that does not make valid metadata end with exit code 7
  instead of 3, naming the file or the series; `fetch` and `transform` check a ZIP the same way
  (#34).
- Staged data built with an older configuration ends with exit code 5, saying it was staged
  with another configuration and naming `transform` as the step to run, instead of 3; a release
  record that is not UTF-8 is treated as an invalid one instead of ending with 3 (#34).
- `bis-prates serve` on a busy port ends with exit code 2 and a message, instead of uvicorn's own
  code; `--port` must be from 1 to 65535 (#34).
- `bis-prates report --countries ' , '` is refused with exit code 2, as the API refuses it, instead
  of writing an empty report (#34).
- A job ended by an error that is not an `Exception`, or whose result or error cannot be
  recorded, is marked failed, so later jobs are no longer refused until a restart (#34).
- With `--start` after all the data, the PNG and the Plotly chart name no line in their legend
  and text alternative (#34).
- The README, the milestones, the decision records, `examples/http_api.ipynb` and CREDITS match
  the code again (#34).

## [0.2.0] — 2026-11-21

Fast analytics, the `config` commands and options from environment variables, and the full
documentation site.

### Added

- `analytics`: the staged observations as fixed-point NumPy arrays, with `snapshot_at` (latest rate,
  rate 12 months earlier and change for every country or area as of any date), `change_history` (the
  12-month change of every observation) and `rate_moves` (every rate change and its size), exact in
  integer arithmetic (#13).
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
- The report's snapshot uses the `analytics` search: about 0.2 s for all 49 countries or areas
  instead of 2.3 s, with identical results (#13).
- Installing the package now also installs NumPy (2.5.5 or later), which `analytics` needs (#13).
- `transform` casts plain numbers in one go and parses any other value exactly as before; the
  staged files are unchanged (#13).
- `SnapshotArgs` no longer takes `ref_area_column`: the snapshot finds the rows of a country
  or area by its series key (#13).
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
- `bis-prates report`: per requested country or area, the latest valid rate, the most recent valid
  rate on or before the same calendar date 12 months earlier, and the change in basis points,
  computed exactly; statuses for an unknown country or area, no valid data and insufficient history,
  and flags for a stale latest or prior rate. Writes `summary.csv`, `summary.json`,
  `policy_rates.png` and `report.md` (#11). A requested code the data does not have gets a warning
  listing the codes it has, with their names.
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

[Unreleased]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/andrea-dm/policy-rate-monitor/releases/tag/v0.1.0
