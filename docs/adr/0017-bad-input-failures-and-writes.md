# 0017 Bad input, failures and writes: strict on the way in, one exit code per action, files replaced whole

- **Status:** Accepted
- **Decided:** 2026-11-22 (#34)

## Context

A review of the code and the docs before release 0.3.0 found that several kinds of bad input and
failure did not end the way the earlier decisions promise:

- the report could name the wrong source release: its provenance was read from the record next to
  the cached ZIP, which every `fetch` rewrites, so after a new `fetch` without a `transform` the
  old figures came with the new release's checksum;
- `conflicting_duplicate.keep` was ignored when the rule was off, and any value other than
  `"first"` meant `"last"`; a typo in a column of `unexpected_value`'s `params.expected` turned the
  check off without a word;
- `Decimal` rounded a value with more than 28 significant digits and read text such as `"1_5"` as
  15, against [0005](0005-exact-decimal-rates.md);
- one blank or non-whole `UNIT_MULT` or `DECIMALS` cell stopped the whole transform, against
  [0011](0011-data-quality-rules.md), and a non-numeric value with a valid status was reported
  twice;
- a corrupt ZIP, a CSV that cannot be parsed and a series row with a blank country or area code
  ended with exit code 3, "please report it", instead of 7, "the source format changed"
  ([0010](0010-cli-and-exit-codes.md));
- every HTTP answer other than 200 was retried and ended in exit 6, "try again later", a 404
  included, and a redirect was never followed;
- staged data built with an older configuration ended with exit 3; `serve` on a busy port let
  uvicorn's own exit code escape, which collides with 1 and 3; and the API told its clients to run
  `bis-prates transform`;
- the staged files and the outputs were written in place, so a `transform` run from the command
  line while `serve` was reading could pair new observations with the old series list, and a full
  disk could leave that mix behind ([0012](0012-elt-raw-and-staged-layers.md));
- the API's job list grew for ever, and a job ended by an error that is not an `Exception` stayed
  "running", so every later job was refused until a restart ([0014](0014-http-api.md));
- requested codes were not checked, so text such as `=HYPERLINK(...)` reached `summary.csv` and
  `report.md` as typed; and the summary table rounded the rates but not the change, so 4.125 and
  3.000 showed as "4.12" and "3.00" next to 112.5 basis points.

## Decision

### The source release and the rules

- **`transform` records the release it staged, in `staged/release.json`**, and the provider's
  `source_release()` reads it from there. The record next to the ZIP is copied only when its
  SHA-256 is that of the ZIP `transform` read: a `fetch` running at the same time may have
  replaced it. With no matching record, nothing is staged as the release (a warning says so) and
  `source_release()` returns `None`, as it does when nothing is staged: the provenance is then
  unknown, never wrong. A release record that cannot be decoded (not UTF-8, or not a valid record)
  is treated like an invalid one, next to the ZIP and in `staged/`: unknown, not a crash.
- **`keep` is read whatever `enabled` says.** Switching `conflicting_duplicate` off only stops its
  findings, as 0011 says of every rule; the row it keeps is still the configured one (`last` when
  the rule or its `keep` is absent). Loading the configuration rejects a `keep` other than
  `"first"` or `"last"`, with exit 4, so `bis-prates config validate` reports it too. The report
  always says which row is kept. The parameters of the built rules are thus checked when the
  configuration is loaded, no longer only by the rule when it runs, as
  [0007](0007-config-driven-design.md) had it.
- **Loading the configuration checks `unexpected_value`'s `params.expected`** (exit 4), whether
  the rule is on or off: it must be an object, each of its columns must be a column of the
  configuration (in `structure` or in `columns.unmapped`), and each of its codes a text. Before, a
  missing `expected` made the rule log a warning and do nothing, and a code that is not a text made
  every row a finding. A column of the configuration that the file does not have is still skipped
  when the rule runs, as before.

### Numbers

- **A value is read only as a plain ASCII number**: a sign, digits, a decimal point and an
  exponent, with ASCII spaces around it ignored (a non-breaking space is not one). It must convert
  to a `Decimal` exactly, with rounding trapped (`Inexact` and `Rounded`), and then fit the
  configured precision and scale, as before. Anything else (`"1_5"`, digits of another script,
  `"Infinity"`, 29 significant digits, a non-breaking space around it) is not a number: the row is
  kept as not valid, with a `non_numeric_value` finding. `UNIT_MULT` is read the same way, as a
  whole number. A date in digits of another script is not in the configured
  format either: it gets the `unparseable_date` finding, as any other date that cannot be read.
- **A blank or non-whole `UNIT_MULT` marks its rows not valid, with a `non_numeric_value`
  finding**: without the multiplier the value cannot be computed. The other rows of the file are
  staged as usual.
- **A blank, non-whole or negative `DECIMALS` leaves the series' decimals unknown, with a warning
  in the log.** `DECIMALS` only formats the rates for display, so the values stay valid; a rate
  with unknown decimals is shown with only the digits it needs (no trailing zeros).
- **`status_value_mismatch` reports a valid status only when the value is missing**, not when it
  is not a number: that is `non_numeric_value`'s finding alone, so a row is counted once.
- **The summary table never rounds a rate**: it pads a rate to the series' decimals and keeps any
  further digit the value has (4.125 with two decimals shows as "4.125", 3 as "3.00"), so the
  rates of a row always add up to its exact change.

### Errors and exit codes

- **Bad source data ends with exit 7** (`UnexpectedFormatError`), naming the file or the series:
  a corrupt, encrypted or unreadable ZIP, a CSV that cannot be parsed or decoded in the configured
  encoding, a CSV with no rows or none of the configured selection (no `FREQ = D` row, say), and
  a series row that does not make valid series metadata (a blank country or area code, say). Only
  the errors these cases raise are caught (zipfile's errors for a bad ZIP, a bad CRC-32, a bad
  compressed stream of any method, a bad offset in the ZIP's directory, a garbled file name, a
  truncated file, an encrypted entry or an unknown compression method, around zipfile calls over
  bytes in memory alone; a CSV parser error, an empty CSV, a decoding error, and the metadata's
  validation error), so a bug elsewhere still ends with 3. `fetch` and `transform` check a ZIP
  with one function, `fetch.read_zip_csv`, so they refuse the same files; it reads the CSV whole,
  checking its CRC-32, before the parser sees it, so no error is raised inside the parser's
  threads.
- **HTTP answers.** The download follows up to five redirects (301, 302, 303, 307 and 308; a sixth
  is refused). A connection error, a timeout, a 5xx, 408 (request timeout) or 429 (too many
  requests) is temporary: it is retried up to `max_attempts` times, then ends with exit 6. Any
  other status but 200 means the source has changed: exit 7 at once, with no retry. A discovery
  page that fails in either way still falls back to the configured `fallback_url`, with a warning.
  A status that is not temporary ends with exit 7 even when an older copy is cached: using it
  would hide that the source has changed. The redirects are followed by hand, not by `urllib`,
  to keep the separate connect and read timeouts. `fetch()` takes the pause and the clock as
  keyword arguments (`sleep`, `now`), so the tests never wait.
- **Staged data that does not match the configured schema** (built with an older configuration)
  raises the missing-input error, exit 5: the message says the file was staged with another
  configuration and names `transform` as the step to run.
- **`MissingInputError` names the step that makes the missing input** (`step`: `fetch` or
  `transform`). Its message says only what is missing; each adapter words the hint its own way:
  the command line adds "run `bis-prates transform` first", the API "run POST /jobs/transform
  first".
- **`--port` must be a whole number from 1 to 65535**, checked by argparse (exit 2). `serve` binds
  the socket itself before uvicorn starts, so a port that cannot be bound (in use, or not allowed)
  ends with exit 2 and a message naming the address and the port, not with uvicorn's own code.
  The error is the web adapter's own `web.ListenError`, not a domain error in `core`; `serve`
  hands uvicorn the very socket it bound, so no other process can take the port in between.
- **Requested codes are checked against the configuration's code pattern** (letters, digits and
  `_ @ $ -`, the pattern the configuration's own codes follow): the command line refuses another
  code with exit 2, the API with `422` `invalid_request`. A code that matches but that the data
  does not have is still `unknown_ref_area`, as before. Both adapters call one check,
  `config.requested_codes`: it strips each code and drops the empty ones, refuses a request left
  with no code, and returns the stripped codes, which the adapters pass on, so the outputs show the
  code that was checked, without the spaces around it. The pattern is written once, as
  `config.CODE_PATTERN`, and matched in full with `re.fullmatch`, after the strip. It keeps out `=`, `+`, brackets, quotes and HTML, so no spreadsheet function or link can
  be built from a code; a leading `-` or `@` is still allowed, because the configuration's codes
  allow it.

### Writes

- **Every staged file and every output is written to a temporary file beside its target, then
  renamed into place** (`core.files.write_files`), so each file is replaced whole. The renames run
  only once every file of the set is written, in a fixed order, with `staged/release.json` last.
  A failure while writing (a full disk) replaces nothing, and the temporary files are removed.
  `fetch` writes the ZIP and its record the same way, the record second.
- **One run of `transform` is one set**: `raw/<zip>.parquet`, `observations.parquet`,
  `series.json`, `data_quality.json` and `release.json` go in one `write_files` call, so a run
  that fails (on a missing column, say) leaves the raw and staged files of the last run as they
  were. With no release record to stage, the old `staged/release.json` is removed before the
  writes: the provenance of the new files is then unknown, never the old one, and a write that
  then fails leaves the previous files with their release unknown.
- **A reader in another process can still pair files of two runs while the renames run.** The
  renames are not one step: a `bis-prates report`, or the first read of `serve`, that reads the
  staged files while a `transform` from the command line renames them can load new observations
  with the old series list. The API's own transform job is not affected: it keeps serving the data
  it has until the job ends. The gap is left open for now: it needs two processes at the same
  moment, and closing it changes the staged layout (#35).
- **One run of `report` is one set** too: the five outputs are made in memory first, then written
  in one call (CSV, JSON, PNG, Markdown, HTML), so a failure while making any of them, the chart
  say, replaces none. The PNG on disk and the one inside `report.html` are the same bytes.
- **The API keeps the last 100 finished jobs** (`KEEP_FINISHED`); the oldest are forgotten first,
  and `GET /jobs/{id}` of a forgotten job gets `404`. Queued and running jobs are always kept.
- **A job is marked failed whatever ends it.** An error that is not an `Exception` (such as
  `KeyboardInterrupt`), a result that is not a valid job result, and an error whose text cannot be
  read all mark the job `failed`, so the next job is not refused for ever. The error is not raised
  again: the worker thread's executor would only keep it on a future that nobody reads.

### Smaller changes in the same release

- The PNG and the Plotly chart take their legend and their text alternative from the lines they
  actually draw (`chart.lines.chart_lines`), so a `--start` after all the data gives "No data to
  plot" with no line named, and the line styles are numbered in the order drawn. With no line,
  the PNG has no legend.
- The bulk provider reads `series.json` again only when the file changes (its modification time
  or size), not once per series.
- The provider's `transform()` returns the counts of the run (`TransformSummary`, a frozen
  model in `core` like the others), which the command line prints, instead of reading the staged
  file again.
- The command line imports the report steps (`service`, with matplotlib and Jinja) only for
  `report` and `serve`, so `--version`, `fetch` and `transform` start without them. pandas and
  pyarrow are still imported by every command, as `cli` imports `bulk` and `transform` when it
  loads: `fetch` and `transform` need them anyway, so deferring them would save time only for
  `--version` and `config`.
- The analytics dataclass over NumPy arrays compares by identity (`eq=False`), the staged data the
  API shares across threads holds tuples, and the emoji map of the report is read-only.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Leave the robustness, speed and test items for 0.3.1 | The release is meant to close the milestone with the known defects fixed |
| The release read from the record next to the ZIP, as before | Every `fetch` rewrites it, so it can name a release that was never staged |
| The release inside `series.json` or the parquet metadata | Couples two files; a small file of its own can be written last, as the mark of a complete set |
| `keep` matched without regard to case, or checked only when the rule runs | One spelling is simpler to document; a value checked only at run time was the defect: a typo meant `last` |
| An unknown id in `params.expected` skipped with a warning | A typo would still turn the check off, in a log nobody reads |
| `Decimal`'s own leniency | A garbled value must not become a rate |
| A larger `Decimal` precision instead of trapping rounding | Any precision still rounds a longer value silently |
| A blank `UNIT_MULT` read as 0 | Would make a value wrong by a power of ten without a word |
| A bad `UNIT_MULT` or `DECIMALS` stopping the transform, as before | One cell would stop every report, against 0011 |
| A finding for a bad `DECIMALS` | It changes no value, only how one is shown; no configured rule covers it |
| Both `non_numeric_value` and `status_value_mismatch` for one row | The same problem counted twice |
| Catching every error while reading the source file as exit 7 | Would turn a bug into "the source format changed"; zipfile's errors, a `ValueError` or an `OSError` among them, are caught only around zipfile calls over bytes in memory, where they can only come from the ZIP |
| Skip malformed rows and count them as a finding, instead of exit 7 | Needs a rule and a row-level parser; exit 7 is honest and enough for now |
| Every non-200 answer, or every 4xx, temporary | A 404 does not go away by retrying, and exit 6 tells a scheduler to try again |
| Using the cached copy when the answer is not temporary | Would hide that the source has changed |
| `urllib`'s own redirect handling | Loses the separate connect and read timeouts |
| Reading `Retry-After` on a 429 | The pauses between attempts (1 s, 2 s) are enough for one small file a day |
| Exit 4 for staged data of an older configuration | The configuration is valid; the data is out of date, and `transform` fixes it |
| The hint "run `bis-prates transform`" inside the message | Wrong for an API client, who runs `POST /jobs/transform` |
| uvicorn's own exit code for a busy port, or a new exit code | Its codes collide with 1 and 3; a port is part of the command line, so 2 says what to fix |
| Catching uvicorn's exit on a busy port | uvicorn only logs the cause, so the message could not say why, and every exit would read as a busy port |
| Trying the port, then closing it before uvicorn starts | Another process can take the port in between |
| A listen error in `core`, or a subclass of `OSError` | It concerns only the web adapter; as an `OSError` an uncaught one would read as exit 8 |
| A file lock between the command line and the server | Writing to a temporary file and renaming is enough to never read a half-written file, needs no lock file that a crash would leave behind, and a lock would make a reader wait on a whole transform |
| A new folder per run, switched in by one rename of a pointer file | Closes the gap for readers in other processes, but changes the staged layout every page and tool names, and adds the pointer and the clean-up of old folders; left to a follow-up (#35) |
| A checksum of each staged file in `release.json`, checked by every reader | Catches a mixed set, but every read hashes every file, and staged data with no release record could not be checked |
| Comparing file stamps before and after a read, and reading again | Narrows the window but does not close it: a read that falls wholly between two renames sees no change; it also adds a method to the provider interface |
| Readers refusing a staged set that has no `release.json` | Staged data with an unknown release is valid (no record next to the ZIP, or staged before this release) |
| Removing the whole set when a rename fails | Deletes good files, report outputs included, to avoid a mix that a reader can meet anyway |
| Turning SIGTERM into a clean exit, to tidy a set stopped between two renames | Nothing is left to tidy once a failed rename no longer removes the set; the temporary files of a stopped run are replaced by the next one |
| Writing each output, or the raw layer, with its own call | A failure halfway would still leave a mix of old and new files |
| Removing a stale `release.json` after the renames | A short window with new data next to the old release |
| Raising an error that is not an `Exception` again once the job is marked | Stops nothing: the executor keeps it on a future that nobody reads |
| Keeping every job, or forgetting jobs after a time | The list grows for ever, or needs a timer; a count bounds the memory |
| Checking the stripped code but passing the typed text on | Two versions of one code, and stray spaces in `summary.csv` |
| Drawing an empty line and hiding it from the legend | Each chart would need its own workaround, and the text alternative the same filter |
| Escaping requested codes in each output instead of checking them | Every output would need its own escaping (CSV formulas, Markdown, HTML); a code is not free text |
| A pattern without a leading `-` or `@` | The configuration's own codes follow the pattern as it is; with no `=`, `+` or bracket, such a cell builds no function or link |
| Reading every Unicode space around a value, as `str.strip()` does | A value padded with a non-breaking space is not a plain ASCII number |
| Rounding the summary table's rates and change to the series' decimals | Rounding is what 0005 rules out, and the rounded change would not match `summary.json` |

## Consequences

- After a new `fetch` without a `transform`, the report names the release that was staged; with
  staged data from before this release (no `staged/release.json`), the report says the release is
  unknown until the next `transform`.
- A configuration with a bad `keep`, or an `unexpected_value` rule whose `params.expected` is
  missing, not an object of codes or names an unknown column, accepted before, now fails to load
  (exit 4), even with the rule off.
- A 404 or 403 from the BIS site ends at once with exit 7; only a temporary failure is retried.
- Writing the staged set needs room on the disk for a second copy while it is written.
- The renames are not one step: a reader in another process whose reads overlap them can pair
  files of two runs (#35). On disk, a `transform` that fails never leaves files of two runs; a
  process ended between two renames (a kill, a power cut) can, until the next run.
- A job id older than the last 100 finished jobs is not found.
- A requested code with other characters than the code pattern's is refused rather than reported
  as unknown.
- A rate in the summary table can show more digits than its series' `DECIMALS`.

## Evidence

- `src/policy_rate_monitor/core/files.py`, `core/errors.py` (`MissingInputError.step`);
  `transform.py` (`load_release`, the number checks, `_unit_multipliers`, `_decimals`);
  `fetch.py` (`http_get`, `MAX_REDIRECTS`, `read_zip_csv`); `bulk.py`; `config/models.py`
  (`conflict_keep`, `CODE_PATTERN`, `requested_codes`); `cli/main.py`; `web/app.py` (`serve`,
  `ListenError`), `web/jobs.py` (`KEEP_FINISHED`), `web/errors.py`; `report/pages.py`.
- `tests/core/test_files.py`: `test_a_failed_write_replaces_nothing`.
- `tests/test_transform.py`: `test_only_a_plain_ascii_number_that_converts_exactly_is_a_value`,
  `test_a_bad_unit_multiplier_makes_its_rows_invalid_not_the_run_fail`,
  `test_a_bad_decimals_leaves_the_decimals_unknown_with_a_warning`,
  `test_a_value_that_is_not_a_number_is_one_finding_not_two`,
  `test_a_source_file_that_cannot_be_read_is_an_unexpected_format`,
  `test_a_series_row_with_no_ref_area_code_is_an_unexpected_format`,
  `test_a_date_in_digits_of_another_script_is_not_a_date`,
  `test_the_release_of_the_zip_read_is_staged_last`, `test_an_unreadable_staged_release_is_unknown`,
  `test_a_failed_write_leaves_the_staged_files_of_the_last_run`,
  `test_the_configured_keep_applies_whether_the_rule_is_on_or_not`.
- `tests/config/test_models.py::test_rule_parameters_are_checked_even_when_the_rule_is_off`.
- `tests/providers/test_bulk.py`: `test_the_release_is_the_one_staged_not_the_one_fetched_last`,
  `test_staged_data_of_another_configuration_says_to_transform_again`,
  `test_series_json_is_read_once_per_change_of_the_file`.
- `tests/test_fetch.py`: `test_an_answer_that_is_not_temporary_fails_at_once`,
  `test_a_temporary_answer_is_retried_then_the_source_is_unavailable`,
  `test_up_to_five_redirects_are_followed`, `test_a_sixth_redirect_is_refused`,
  `test_a_failed_discovery_page_falls_back_to_the_configured_url`,
  `test_unexpected_download_fails_and_keeps_the_cached_copy`,
  `test_an_unreadable_release_record_is_ignored`.
- `tests/test_cli.py`: `test_a_missing_input_gives_a_one_line_error_naming_the_step`,
  `test_report_refuses_a_code_outside_the_code_pattern`,
  `test_report_refuses_a_list_with_no_code_and_writes_nothing`,
  `test_serve_refuses_a_port_outside_1_to_65535`,
  `test_serve_on_a_busy_port_exits_2_naming_the_host_and_port`.
- `tests/web/test_app.py::test_a_code_outside_the_code_pattern_gets_422`; `tests/web/test_jobs.py`:
  `test_a_job_stopped_by_a_base_exception_is_failed_and_the_next_one_runs`,
  `test_a_job_whose_end_cannot_be_recorded_as_is_is_still_failed`,
  `test_a_report_job_refuses_a_code_outside_the_code_pattern`,
  `test_the_runner_keeps_the_last_finished_jobs_and_the_running_one`.
- `tests/test_report.py`: `test_the_summary_table_pads_a_rate_but_never_rounds_it`,
  `test_a_failed_report_leaves_the_previous_files_as_they_were`.
