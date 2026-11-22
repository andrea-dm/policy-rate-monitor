# Interfaces

Every way the system is entered or exchanges data: the command line, the HTTP API, the provider
interface, the configuration schema and the file formats. Each section states the contract and
points to the page that has the full detail.

## The command line

The entry point is `bis-prates = policy_rate_monitor.cli.main:main` (`pyproject.toml`);
`python -m policy_rate_monitor.cli` is equivalent.

| Command | Reads | Writes | Prints to standard output |
|---|---|---|---|
| `fetch` | the configuration; the BIS site (or the cached ZIP) | `data/raw/<file>.zip`, `.json` | the address and SHA-256 of the release |
| `transform` | the configuration; `data/raw/<file>.zip` and its record | `data/raw/<file>.parquet`, `data/staged/*` | observations, series and findings kept |
| `report --countries ...` | the configuration; `data/staged/*` | `out/*` (five files) | the five paths |
| `serve` | the configuration; the staged data when a request needs it | what the jobs write | nothing of its own; uvicorn's default access log (one line per request) goes to standard output, the rest of the log to standard error |
| `config validate` | the configuration | nothing | the path, `valid` and where the path came from |
| `config schema` | nothing | nothing | the JSON Schema of the configuration |

Guarantees of the interface:

- Results go to standard output; warnings, errors and the log go to standard error.
- The exit code identifies the kind of failure ([Failure modes](failure_modes.md#exit-codes-and-http-statuses)).
- The configuration file and the data directory come from the flag, then the environment variable,
  then the default ([`settings`](components.md#settings)).
- `--countries` is checked against the code pattern before anything runs (exit 2).
- Options, defaults and examples are in the [CLI reference](../../cli.md).

## The HTTP API

Built by `web.app.create_app`; started by `bis-prates serve`. FastAPI serves the OpenAPI document at
`/openapi.json` and interactive pages at `/docs` and `/redoc`.

| Method and path | Success | Body |
|---|---|---|
| `GET /ref-areas` | 200 | list of `{ref_area, ref_area_name, aliases}` |
| `GET /summary?countries=` | 200 | the `summary.json` document (`generated_at`, `source`, `dataset`, `snapshots`) |
| `GET /timeseries?country=&start=` | 200 | `{requested_ref_area, series, observations[{date, value}]}` |
| `GET /chart?countries=&start=` | 200 | a Plotly figure as JSON |
| `POST /jobs/fetch` | 202 | the job; `Location: /jobs/{id}` |
| `POST /jobs/transform` | 202 | the job; `Location: /jobs/{id}` |
| `POST /jobs/report` | 202 | the job; body `{countries: [...], start: date or null}` |
| `GET /jobs/{id}` | 200 | the job: `id`, `kind`, `status`, `created_at`, `started_at`, `finished_at`, `result`, `error` |

Guarantees:

- Every error response, and the `error` of a failed job, has the body
  `{"error": <kind>, "message": <text>}` and never a traceback
  (`web/errors.py`, `add_error_handlers`). The kinds and statuses are in
  [Failure modes](failure_modes.md#exit-codes-and-http-statuses).
- `GET /summary` returns what `report` writes to `summary.json`, apart from `generated_at`, which
  is the time of the request, because both call `service.summary` and
  `report.summary_document`.
- Requested codes are checked with the same `requested_codes` as the CLI; a blank list or a code
  outside the pattern is `422`.
- Rates in the JSON are numbers, not strings. A client that parses them as binary floating point
  loses the exactness of the source ([Data model](data_model.md#exact-decimals)).
- A job accepted is a job that runs, except a job still queued when the service stops, which is
  cancelled (`web/jobs.py` `JobRunner.shutdown`): `POST` answers `202` and the outcome is read
  with `GET /jobs/{id}`. The rules of the job runner are in
  [Runtime behaviour](runtime_behavior.md#the-api-process).
- The API sends no CORS headers and has no authentication ([Security](security.md#the-apis-exposure)).

The endpoint reference, with examples, is [HTTP API](../../api.md).

## The provider interface

`DataProvider` (`providers/base.py`) is the seam between the data and everything above it
([decision 0008](../../adr/0008-provider-interface.md)).

| Method | Kind | Returns | Raises |
|---|---|---|---|
| `fetch()` | lifecycle | `SourceRelease` | `SourceUnavailableError`, `UnexpectedFormatError` |
| `transform()` | lifecycle | `TransformSummary` | `MissingInputError` (step `fetch`), `UnexpectedFormatError` |
| `list_series()` | query | the series keys, sorted | `MissingInputError` |
| `series_metadata(key)` | query | `SeriesMetadata` | `KeyError`, `MissingInputError` |
| `observations(keys=None)` | query | an Arrow table in `observation_schema(config.structure)` | `MissingInputError` |
| `data_quality_issues()` | query | the findings of the last `transform` | `MissingInputError` |
| `source_release()` | query | `SourceRelease` or `None` | nothing |

Obligations of an implementation, from the `DataProvider` docstrings (`providers/base.py`):

- the table has exactly the canonical schema, with at most one row per series and date, sorted by
  series and date;
- an unknown key in `observations(keys)` adds no row;
- a failed `transform` leaves the previous canonical data as it was;
- `MissingInputError.step` names the lifecycle method to run.

What `ProviderContract` in `tests/providers/contract.py` checks on every provider: the canonical
schema, one row per series and date, dates rising within each series, `is_valid` set with a value
on every valid row, metadata for every series, the query of one series, the release returned by
`fetch` and `source_release`, the counts returned by `transform`, and findings that are
`DataQualityIssue` records. The bulk provider's `step` and its answer to data staged with another
configuration are checked in `tests/providers/test_bulk.py`
(`test_queries_before_transform_say_which_step_to_run`,
`test_staged_data_of_another_configuration_says_to_transform_again`).

A worked example of a new provider is in
[Architecture](../../architecture.md#a-new-source).

## The configuration schema

One JSON document per dataset (`configs/bis_cbpol.json` is the shipped one). Its contract is the
pydantic models of `config.models`, and `configs/config.schema.json` is their JSON Schema
(draft 2020-12), regenerated with `bis-prates config schema`. `tests/config/test_schema.py` fails
when the committed file differs from what the models produce, so the schema cannot drift.

Top-level sections, all required: `dataset`, `source`, `structure`, `columns`, `rules`,
`snapshot`, `output`, `ref_area_aliases`. An optional `$schema` key is accepted for editors. No
other key is accepted in any section or object of the models (`extra="forbid"` on
`_ConfigModel`). The exception is a rule's `params`, a free JSON object (`RuleSpec.params`,
`config/models.py`). Only the parameters of the built rules are checked: `keep` is `first` or
`last`, and `expected` maps declared columns to codes. Any other parameter name is accepted and
ignored. The meaning of each section is in
[Configuration](../../configuration.md).

| Section | What it fixes |
|---|---|
| `dataset` | identity, the report's texts, the default data directory |
| `source` | the discovery page, the file name, the fallback URL, the cache lifetime, the attempts and the timeouts |
| `structure` | the dimensions, the time dimension and its date format, the measure with its precision and scale, the attributes, the series metadata mapping, the selection |
| `columns` | encoding, delimiter, label separators, missing-value texts, unmapped and optional columns |
| `rules` | the data-quality rules: name, `enabled`, severity, parameters |
| `snapshot` | the look-back in months and the two staleness thresholds |
| `output` | the series fields carried by each summary row |
| `ref_area_aliases` | codes people use for codes the source uses (`EA` for `XM`) |

A change to the models needs a regenerated schema in the same commit; the CLI reference describes
the command.

## The file formats

| Path | Format | Written by | Content |
|---|---|---|---|
| `data/raw/<file_name>` (`.zip`) | ZIP, as downloaded | `fetch` | the source, unchanged |
| `data/raw/<stem>.json` | JSON, 2-space indent | `fetch` | `SourceRelease`: `location`, `version_id`, `checksum_sha256`, `retrieved_at` |
| `data/raw/<stem>.parquet` | parquet | `transform` | every row and column of the CSV, as text |
| `data/staged/observations.parquet` | parquet | `transform` | the canonical table ([Data model](data_model.md#the-canonical-observations-table)) |
| `data/staged/series.json` | JSON array, 2-space indent | `transform` | one `SeriesMetadata` per series |
| `data/staged/data_quality.json` | JSON array, 2-space indent | `transform` | one `DataQualityIssue` per finding |
| `data/staged/release.json` | JSON, 2-space indent | `transform`, last | the `SourceRelease` the staged files were built from; absent when unknown |
| `out/summary.csv` | CSV, UTF-8 | `report` | one row per distinct requested code (codes are compared after stripping and upper-casing), in request order; rows end with CRLF (Python's `csv` default); rates as plain decimal text |
| `out/summary.json` | JSON, UTF-8, 2-space indent, final newline | `report` | the `summary_document` |
| `out/policy_rates.png` | PNG, 120 dpi | `report` | the chart; the PNG metadata carry the title and the text alternative |
| `out/report.md` | Markdown, UTF-8 | `report` | the report; refers to the chart by the file name `policy_rates.png` |
| `out/report.html` | HTML, UTF-8 | `report` | the report, self-contained: the chart and its script are inline |

Compatibility rules the formats follow:

- Dates in the JSON are ISO 8601 (`YYYY-MM-DD`); timestamps carry a time zone (`AwareDatetime`).
- Rates in `summary.json` and the API are JSON numbers. Read them as decimals, as the
  [Data model](../../data-model.md#outputs) shows.
- The staged parquet files keep their Arrow types, so `value` stays `decimal128` and `date` stays
  `date32`. `BulkFileProvider.observations` casts the table to the configured schema and reports a
  mismatch as a missing input with step `transform`.
- No file carries a format version. A change of the models or the configuration that alters the
  staged layout is detected only through that cast; the other staged files are read through the
  models, which refuse unknown fields.
