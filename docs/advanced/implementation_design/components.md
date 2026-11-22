# Components

The contract of each public module: what it takes, what it returns, what it raises, what it reads
and writes, and which tests pin the behaviour. Signatures and parameters are in the
[API reference](../../reference.md); this page states what a caller can rely on. The modules are
in layer order, from the foundation up ([System overview](system_overview.md#the-layering-rule)).

Notation: "raises" lists the typed errors of `core.errors`; every module can also let an `OSError`
through, which the adapters report as a file error ([Failure modes](failure_modes.md)).

## `core`

[`core.models`](../../reference.md#policy_rate_monitor.core.models),
[`core.errors`](../../reference.md#policy_rate_monitor.core.errors),
[`core.files`](../../reference.md#policy_rate_monitor.core.files).

- **Contract.** The domain models exchanged between the layers (`SeriesMetadata`, `DataSnapshot`,
  `DataQualityIssue`, `SourceRelease`, `TransformSummary`, and the enums `SnapshotStatus`,
  `SnapshotFlag`, `DataQualitySeverity`), the five typed errors, and `write_files`. Models are
  frozen, forbid unknown fields and strip whitespace; their invariants are checked at
  construction ([Data model](data_model.md#invariants)).
- **Reads and writes.** Only `write_files` touches the disk: it writes the given files to
  `.<name>.tmp` beside their targets and renames them into place in the order given
  ([Runtime behaviour](runtime_behavior.md#writing-a-set-of-files)).
- **Imports.** Nothing from the package.
- **Tests.** `tests/core/test_models.py`, `tests/core/test_errors.py`, `tests/core/test_files.py`.

## `config`

[`config.models`](../../reference.md#policy_rate_monitor.config.models),
[`config.parsing`](../../reference.md#policy_rate_monitor.config.parsing),
[`config.source`](../../reference.md#policy_rate_monitor.config.source),
[`config.schema`](../../reference.md#policy_rate_monitor.config.schema).

- **Contract.** `ConfigSource.load()` returns a validated `DatasetConfig` or raises `ConfigError`.
  `FileConfigSource` reads a UTF-8 JSON file; `parse_config(document, origin=...)` is the one
  parser, so every source gives the same checks and messages. `requested_codes` checks the codes a
  user asks for against `CODE_PATTERN` (`^[A-Za-z0-9_@$-]+$`). `render_json_schema` renders the
  JSON Schema of the models in one fixed text form.
- **Checks.** Unknown keys, duplicate keys, `NaN` and `Infinity`, and too deep nesting are refused;
  a date format must round-trip two test dates; an encoding must be a text encoding; `file_name`
  cannot hold a path separator; `data_dir` cannot start with `~` or hold `$`; rules, aliases,
  selections and output fields are cross-checked against the declared structure. Problems are
  reported together, each at its location in the JSON.
- **Reads and writes.** `FileConfigSource.load` reads one file; nothing else in the module does
  I/O.
- **Raises.** `ConfigError`, with the origin first in the message.
- **Tests.** `tests/config/` (`test_models.py`, `test_parsing.py`, `test_source.py`,
  `test_schema.py`, `test_bis_cbpol.py`); `test_schema.py` compares the committed
  `configs/config.schema.json` with the schema the models produce.

## `fetch`

[`fetch`](../../reference.md#policy_rate_monitor.fetch).

- **Contract.** `fetch(source, data_dir)` returns the `SourceRelease` of a cached ZIP that is
  younger than `cache_ttl_hours`, of a fresh download, or, when the network fails, of an older
  cached copy (with a warning). It finds the ZIP's link on the discovery page by exact file name,
  and uses the fallback URL when the page cannot be read or has no such link. A download is
  accepted only when it is a ZIP with exactly one `.csv` entry and a good CRC-32
  (`read_zip_csv`).
- **Retries.** Only `SourceUnavailableError` is retried: up to `max_attempts` attempts, with a
  pause of 1 s, then 2 s, and so on, between attempts. The discovery page is requested once.
- **Reads and writes.** `data/raw/<file_name>` and its `.json` record, through `write_files`: the
  ZIP first, the record second.
- **Raises.** `SourceUnavailableError` (nothing cached and the source unreachable or answering
  5xx, 408 or 429); `UnexpectedFormatError` (any other status but 200, more than five redirects,
  a download that is not a ZIP with one CSV, or an address that is not HTTP(S)). A cached copy is
  kept in every case.
- **Tests.** `tests/test_fetch.py`.

## `transform`

[`transform`](../../reference.md#policy_rate_monitor.transform).

- **Contract.** `transform(config, data_dir, reference=NO_REFERENCE)` loads the CSV in the cached ZIP into
  `data/raw/<stem>.parquet` (every column as text, as loaded), stages the daily series, and returns
  a `TransformSummary`. It writes `staged/observations.parquet`, `series.json`,
  `data_quality.json` and, when the record next to the ZIP has the checksum of the ZIP read,
  `release.json` last. The `load_*` functions read the staged files back and raise
  `MissingInputError` (step `transform`) when one is absent.
- **Rules.** The rules that have code are listed in `IMPLEMENTED_RULES`: `unexpected_column`,
  `unparseable_date`, `non_numeric_value`, `status_value_mismatch`, `exact_duplicate`,
  `conflicting_duplicate`, `unexpected_value`, and the two SDMX rules `ref_area_not_in_code_list`
  and `dataflow_mismatch`, which ask `reference` once each, after the file is read and its
  selection has rows; a `ReferenceUnavailableError` becomes a "not evaluated" finding. The shipped
  configuration also lists
  `excess_decimals`, `series_attribute_not_constant` and `plausible_range`; `transform` logs
  "data-quality rule ... is not implemented yet" for each and skips it. A rule set to
  `enabled: false` produces no finding. The rules and their findings are explained in
  [Data quality](../../data-quality.md#3-the-rules).
- **Reads and writes.** `data/raw/` and `data/staged/`. The whole ZIP, then the whole CSV, are
  held in memory as bytes before parsing.
- **Raises.** `MissingInputError` (ZIP absent); `UnexpectedFormatError` (corrupt ZIP, a CSV that
  cannot be parsed or decoded or is empty, no row of the configured selection, a required column
  missing, a series row that is not valid metadata).
- **Tests.** `tests/test_transform.py`, `tests/test_transform_sdmx.py`.

## `reference`

[`reference`](../../reference.md#policy_rate_monitor.reference).

- **Contract.** `ReferenceSource` answers `structure()` (the components of the structure the
  configured dataflow points to, without the time dimension) and `ref_area_names()` (the codes, with their names, of
  the configured code list). It raises `ReferenceUnavailableError` when it cannot answer, and
  nothing else on purpose. `UnavailableReference(reason)` always raises with its reason;
  `NO_REFERENCE` is the default. A source keeps nothing between calls.
- **Reads and writes.** None in this module.
- **Tests.** `tests/test_transform_sdmx.py`, with `tests/fake_reference.py`.

## `sdmx`

[`sdmx`](../../reference.md#policy_rate_monitor.sdmx).

- **Contract.** `SdmxReference(reference_section)` asks the configured API with `pysdmx`'s
  registry client: the dataflow, then the structure it names, then the code list, one attempt
  each with the configured timeout. Every `pysdmx` error and an answer that cannot be decoded
  become `ReferenceUnavailableError` with a plain reason. Needs the `sdmx` extra.
- **Reads and writes.** The SDMX API; nothing on disk.
- **Tests.** `tests/test_sdmx.py` (recorded answers in `tests/data/sdmx/`), and
  `tests/integration/test_sdmx_api.py` against the live API (`pytest -m integration`).

## `providers`

[`providers.base`](../../reference.md#policy_rate_monitor.providers.base),
[`providers.schema`](../../reference.md#policy_rate_monitor.providers.schema),
[`providers.memory`](../../reference.md#policy_rate_monitor.providers.memory).

- **Contract.** `DataProvider` has two lifecycle methods (`fetch`, `transform`) and five query
  methods (`list_series`, `series_metadata`, `observations`, `data_quality_issues`,
  `source_release`). The query methods raise `MissingInputError` when nothing is prepared, with
  `step` naming the method to run; `source_release` raises nothing and returns `None` when the
  release is unknown. `observations` returns exactly `observation_schema(config.structure)`, at
  most one row per series and date, sorted by series and date. `InMemoryProvider` serves rows given
  in code, for tests; its `fetch` and `transform` do nothing.
- **Reads and writes.** None in this module.
- **Tests.** `tests/providers/contract.py` holds the contract checks; `test_contract.py` checks
  that each table check fails on a table that breaks it and that a provider missing a method
  cannot be created, and `test_memory.py` and `test_bulk.py` run
  the contract on the two providers.

## `bulk`

[`bulk`](../../reference.md#policy_rate_monitor.bulk).

- **Contract.** `BulkFileProvider(config, data_dir, get=None, reference=NO_REFERENCE)` is the
  provider over the bulk-download file:
  `fetch` and `transform` call the functions of the same name, and the query methods read
  `data/staged/`. `source_release` returns the release staged with the observations
  (`staged/release.json`), not the one next to the ZIP. `observations` casts the stored table to
  the configured schema; a table that does not fit (staged with an older configuration) raises
  `MissingInputError` with step `transform`. `series.json` is read again only when its modified
  time or size changes.
- **Tests.** `tests/providers/test_bulk.py`.

## `analytics`

[`analytics`](../../reference.md#policy_rate_monitor.analytics).

- **Contract.** `rate_history` turns the valid observations into read-only fixed-point arrays
  (dates as int32 days since 1970-01-01, values as int64 in units of 10^-scale percent).
  `snapshot_at` finds, for every series, the last observation on or before a date and the last one
  on or before that observation's date minus N months. `change_history` gives the change against N
  months earlier for every observation; `rate_moves` the observations where the rate differs from
  the previous one; `months_back` is the calendar subtraction (a day that does not exist becomes
  the last day of the month); `bps` turns a fixed-point difference back into basis points as a
  `Decimal`. `change_history`, `rate_moves` and `bps` have no caller in the package today. They
  are built for the web front end, milestone M8, not built
  ([decision 0013](../../adr/0013-fast-analytics-numpy-numba.md)), and are exercised by
  `tests/test_analytics.py` and `diagnostics/bench_analytics.py`.
- **Numba.** With `numba` installed the two searches are compiled with `njit(cache=True)`;
  without it, the NumPy versions run. `NUMBA_AVAILABLE` says which. Both give the same arrays
  (`tests/test_analytics.py`).
- **Reads and writes.** No data file. Pure functions, except that with the `fast` extra Numba
  writes its compiled-code cache (`njit(cache=True)`) on the first call: beside the package in
  `__pycache__`, or in its own cache folder when that one cannot be written.
- **Raises.** `TypeError` from `rate_history` when the values are not Arrow decimals; `KeyError`
  from `RateHistory.position` for an unknown series.

## `snapshot`

[`snapshot`](../../reference.md#policy_rate_monitor.snapshot).

- **Contract.** `compute_snapshots(SnapshotArgs)` returns one `DataSnapshot` per distinct requested
  code, in request order. A code is stripped, upper-cased, de-duplicated and mapped through the
  aliases. The outcome is one of four statuses: `ok`, `unknown_ref_area`, `no_valid_data`,
  `insufficient_history`. Two flags can be added, never suppressing a value: `stale_latest` (the
  latest observation is more than `stale_latest_days` before the newest date in the staged
  observations, all staged series, valid or not) and `stale_prior` (the prior one is more than `stale_prior_days` before the target
  date). The change is `(latest - prior) * 100` on exact decimals.
- **Raises.** `TypeError` when the values are not Arrow decimals.
- **Tests.** `tests/test_snapshot.py`, and `diagnostics/bench_analytics.py` checks the results
  against the earlier `bisect` method and a `Decimal` loop.

## `chart`

[`chart.lines`](../../reference.md#policy_rate_monitor.chart.lines),
[`chart.style`](../../reference.md#policy_rate_monitor.chart.style),
[`chart.static`](../../reference.md#policy_rate_monitor.chart.static),
[`chart.interactive`](../../reference.md#policy_rate_monitor.chart.interactive).

- **Contract.** `lines` decides which series get a line (statuses `ok` and
  `insufficient_history`) and which rows each draws (the valid ones from `start` on), for both
  charts and for the API's time series. `style` holds the colour-blind palette, the dash patterns,
  the label positions, the text alternative and the "No data to plot" text, and imports no plotting
  library. `static.rate_chart` returns a matplotlib figure (base install); `interactive.rate_figure`
  returns a Plotly figure and needs the `interactive` extra.
- **Note.** The charts are drawn from floating-point values; the numbers of the summary files and
  the API stay exact ([HTTP API](../../api.md#get-chart)).
- **Tests.** `tests/chart/test_style.py`, `tests/chart/test_interactive.py`, `tests/test_report.py`.

## `report`

[`report.export`](../../reference.md#policy_rate_monitor.report.export),
[`report.summary`](../../reference.md#policy_rate_monitor.report.summary),
[`report.pages`](../../reference.md#policy_rate_monitor.report.pages).

- **Contract.** `Report(config).export(ReportArgs)` builds `summary.csv`, `summary.json`,
  `policy_rates.png`, `report.md` and `report.html` in memory and writes them together with
  `write_files`, returning their paths. `summary_document` builds the document of `summary.json`,
  which `GET /summary` also returns. Both pages are filled from one context; Jinja autoescaping is
  on for the HTML page and off for the Markdown page, where only `|` is escaped in a table cell. The
  HTML chart carries its JavaScript inline, so the page works offline, and is replaced by the PNG,
  embedded as a data URI, when Plotly is not installed.
- **Reads and writes.** The templates in the package (`report/templates/`); the five files in
  `out_dir`, created if absent.
- **Tests.** `tests/test_report.py`.

## `service`

[`service`](../../reference.md#policy_rate_monitor.service).

- **Contract.** The steps both adapters call: `load_staged(provider)` reads the observations (as a
  pandas table with Arrow types), the series, the findings and the release into a
  `StagedData`, a frozen dataclass: the series and findings are tuples, and the observations table
  is never changed after it is read (the calculations filter or copy it), so request threads share
  one safely; `summary`, `ref_areas`, `timeseries` and `write_report` compute from it.
- **Raises.** `MissingInputError` from `load_staged`.
- **Tests.** `tests/test_service.py`.

## `web`

[`web.app`](../../reference.md#policy_rate_monitor.web.app),
[`web.jobs`](../../reference.md#policy_rate_monitor.web.jobs),
[`web.errors`](../../reference.md#policy_rate_monitor.web.errors).

- **Contract.** `create_app(config, provider, out_dir)` builds the FastAPI application: four read
  routes, three job routes and the job status route, and one error body for every failure.
  `serve(...)` binds the socket itself, so a port in use is a `ListenError`, then runs uvicorn on
  it. `JobRunner` runs one job at a time on one worker thread. Endpoint behaviour is in
  [HTTP API](../../api.md); [Interfaces](interfaces.md#the-http-api) and
  [Runtime behaviour](runtime_behavior.md#the-api-process) state the guarantees.
- **Needs.** The `web` extra.
- **Tests.** `tests/web/test_app.py`, `tests/web/test_jobs.py`.

## `settings`

[`settings`](../../reference.md#policy_rate_monitor.settings).

- **Contract.** `config_path(flag, environ)` and `data_dir(flag, environ, config)` return a
  `Setting` (the path and its `Origin`): the flag, else the environment variable
  (`BIS_PRATES_CONFIG`, `BIS_PRATES_DATA_DIR`; an empty one counts as unset), else
  `configs/bis_cbpol.json` or `dataset.data_dir`. Pure: the environment is passed in.
- **Tests.** `tests/test_settings.py`.

## `cli`

[`cli.main`](../../reference.md#policy_rate_monitor.cli.main),
[`cli.exit_codes`](../../reference.md#policy_rate_monitor.cli.exit_codes).

- **Contract.** `main(argv, environ)` parses the arguments, runs one command and returns an exit
  code; it never raises for an error of the program itself, except argparse's `SystemExit` for
  `--help`, `--version` and bad arguments, and the `KeyboardInterrupt` that uvicorn raises again
  after `serve` is stopped with Ctrl+C (SIGTERM ends the process with status 143 instead;
  [Runtime behaviour](runtime_behavior.md#start-and-stop)). It sets up logging (INFO, to standard error), prints
  results to standard output and errors to standard error. `exit_code_for` maps an error to its
  code by walking its parent classes; anything unmapped is `INTERNAL_ERROR`. `transform` and
  `serve` give the provider an `SdmxReference` when an SDMX rule is enabled, or an
  `UnavailableReference` naming the `sdmx` extra when `pysdmx` is not installed.
- **Tests.** `tests/test_cli.py`.
