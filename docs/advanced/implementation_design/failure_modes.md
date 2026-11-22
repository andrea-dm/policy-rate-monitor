# Failure modes

Every error the system raises on purpose, the exit code and the HTTP status each one becomes, and
what is left on disk after it. The errors are defined in [`core/errors.py`](../../reference.md#policy_rate_monitor.core.errors) and carry no code or
status: `cli/exit_codes.py` and `web/errors.py` map them, so `core` imports nothing above it
([decision 0010](../../adr/0010-cli-and-exit-codes.md),
[0017](../../adr/0017-bad-input-failures-and-writes.md)). The user-level table of exit codes, with
what to do for each, is in the [CLI reference](../../cli.md#exit-codes); the error bodies are in
the [HTTP API](../../api.md#errors).

## Exit codes and HTTP statuses

The mapping walks the error's parent classes, so a subclass gets the code of its nearest mapped
parent; anything not mapped is a bug (`exit_code_for`, `web.errors._kind`).

| Error | Raised when | CLI exit code | HTTP kind | HTTP status |
|---|---|---|---|---|
| `ConfigError` | the configuration is missing, unreadable, not UTF-8 JSON, or does not validate | 4 `CONFIG_ERROR` | `config_error` | 500 |
| `MissingInputError` | a step's input is absent: the ZIP before `transform`, the staged files before `report`; staged data built with another configuration; (API) nothing staged while the first transform runs | 5 `MISSING_INPUT` | `missing_input` | 409 |
| `SourceUnavailableError` | no connection, a timeout, a 5xx, 408 or 429 after `max_attempts`, and nothing cached | 6 `SOURCE_UNAVAILABLE` | `source_unavailable` | 503 |
| `UnexpectedFormatError` | an HTTP status such as 404, too many redirects, a download that is not a ZIP with one CSV, a CSV that cannot be read, a missing required column, no row of the selection, an invalid series row | 7 `UNEXPECTED_FORMAT` | `unexpected_format` | 500 |
| `OSError` | a file cannot be read or written | 8 `OUTPUT_ERROR` | `file_error` | 500 |
| any other exception | a bug | 3 `INTERNAL_ERROR` | `internal_error` | 500 |

Other outcomes with their own code or status:

| Outcome | CLI | HTTP |
|---|---|---|
| No requested country or area has status `ok` | 1 `NO_RESULT`; the five files are still written | the snapshots carry the statuses; no error |
| A code outside the pattern, a port outside 1 to 65535, `serve` without the `web` extra, a port `serve` cannot bind | 2 `USAGE_ERROR` | `invalid_request`, 422, for a code or a body |
| A job is queued or running | - | `job_running`, 409 |
| The service is stopping | exit 130 after Ctrl+C (with a `KeyboardInterrupt` traceback), 143 after SIGTERM (`uvicorn/server.py` `capture_signals`; `cli/main.py` `_serve` does not return) | new connections refused; `shutting_down`, 503, only outside `bis-prates serve` (`web/jobs.py` `JobRunner.submit`; [Runtime behaviour](runtime_behavior.md#start-and-stop)) |
| An unknown path, job id or code | - | `not_found`, 404 |
| A wrong method | - | `http_error`, 405, with `Allow` |

Rules that hold across the table:

- **Only 6 means "try again later".** The other codes need a change: a command, the configuration,
  the source or the disk.
- **A message is for a person.** The CLI prints `error: <message>`. The message is one line, except
  for a configuration error, which lists every problem (or quotes the line of a JSON syntax error
  with a caret) and ends with a line saying where the path came from (`config/parsing.py`,
  `cli/main.py` `_load`), and an invalid series row, whose message carries pydantic's validation
  report over several lines (`transform._series_metadata`). A file error reads `error: cannot read or write a file: ...`. A `MissingInputError` with a `step`
  ends with the command to run on the command line (`run \`bis-prates transform\` first`) and with
  the job to run over HTTP (`run POST /jobs/transform first`).
- **A bug is not hidden and not leaked.** Its traceback is logged in both adapters; the CLI prints
  `error: <Type>: <message>`, the API's body carries only the type name, so no detail of the
  server's internals reaches a client (`web.errors.error_body`).
- **`--debug`** adds the traceback of a typed error or an `OSError` to the log; a bug always has
  it.
- **A failed job is a result.** `GET /jobs/{id}` answers 200 and the job's `error` holds the same
  body as a response.
- **The `config_error` response is defined but not met in a request:** `serve` loads the
  configuration once, before it listens, and no route reads it again.

## What a failure leaves on disk

| Step | Failure | `data/raw/` | `data/staged/` | `out/` |
|---|---|---|---|---|
| `fetch` | source unreachable, a cached copy exists | unchanged; the cached release is used, with a warning (success, exit 0) | unchanged | - |
| `fetch` | source unreachable, no copy | nothing written | unchanged | - |
| `fetch` | a status other than 200, too many redirects, not a ZIP with one CSV | unchanged; a cached copy is kept | unchanged | - |
| `fetch` | write fails before any rename (a full disk) | unchanged; temporary files removed | unchanged | - |
| `transform` | ZIP absent, corrupt or not readable as the configuration says; invalid series row | unchanged | unchanged | - |
| `transform` | write fails before any rename | unchanged; temporary files removed | unchanged, except that `release.json` is already removed when there was no release to stage | - |
| `report` | nothing staged, or staged with another configuration | - | unchanged | not touched |
| `report` | write fails before any rename | - | - | the previous files; temporary files removed |
| any | a rename fails part-way | see below | see below | see below |
| `serve` | the port cannot be bound | - | - | - |

A rename failing part-way is the one case that is not rolled back. `write_files` renames the
temporary files one after the other, and its renames are outside the `try` that cleans up after a
failed write. If one raises `OSError`, the files renamed before it are already the new ones, and
the files after it stay as temporary `.<name>.tmp` files beside their targets, which are the old
ones. The command ends with exit 8.

What the design does so that such a mix is detected, not silently used:

- a `release.json` that is not that of the ZIP `transform` read is not staged, so the outputs never
  name a release the data was not built from;
- `BulkFileProvider.observations` casts the stored table to the configured schema, and a table that
  does not fit is a `MissingInputError` with step `transform`;
- the other staged files (`series.json`, `data_quality.json`) are read through models that refuse
  unknown fields. A file that does not validate raises pydantic's `ValidationError`, which is not
  a typed error: the CLI ends with exit 3 `INTERNAL_ERROR` and the API answers `internal_error`
  (500), with the traceback in the log (`transform.load_series`, `load_data_quality`;
  `cli/main.py` `main`; `web/errors.py` `error_body`). Running `transform` again fixes it.

It does not detect a mix of two runs of the same configuration (the known gap,
[Runtime behaviour](runtime_behavior.md#writing-a-set-of-files)).

## What does not fail a run

Bad data does not stop a run; it becomes a finding or a flag, and the output says so:

| Situation | Result |
|---|---|
| A missing value published as such (`NaN` with the status `M`) | the row is kept, not valid; it is not a finding |
| A value that is not a number, a blank or non-whole `UNIT_MULT`, an unparseable date, duplicates, an unexpected column or value, a status that contradicts the value | a `DataQualityIssue` in `data_quality.json` and the report; the row is dropped or kept invalid ([Data quality](../../data-quality.md#3-the-rules)) |
| A blank, non-whole or negative `DECIMALS` | the decimals are unknown (`null`), with a warning in the log |
| An optional column absent from the file | a warning in the log |
| A rule named in the configuration that the code does not implement | an `info` line in the log; the rule is skipped |
| A latest or prior observation older than the thresholds | a `stale_latest` or `stale_prior` flag; the value is still reported |
| A requested code the data does not have | status `unknown_ref_area`; the CLI prints a warning listing the codes available |

The rule names, the findings and the flags are explained in
[How it works](../../how-it-works.md#4-missing-values-duplicates-and-inconsistent-data-12-33)
and [How it works, section 8](../../how-it-works.md#8-countries-or-areas-the-tool-cannot-answer-for-13).

## Tests

`tests/test_cli.py` (exit codes, messages, a busy port, an output folder that is a file),
`tests/test_fetch.py` (retries, statuses, redirects, bad ZIPs, the cached copy),
`tests/test_transform.py` (typed errors, findings, a failed write leaving the previous staged
files), `tests/test_report.py` (a failed report leaving the previous files),
`tests/core/test_files.py`, `tests/core/test_errors.py`, `tests/web/test_app.py` and
`tests/web/test_jobs.py` (statuses, error bodies, failed jobs).
