# 0010 A command line first, with one exit code per action the caller must take

- **Status:** Accepted
- **Decided:** 2026-11-17 (the CLI as the interface); 2026-11-18 (errors and exit codes)

## Context

The brief offers two interfaces: a command line (Option A, recommended) or an HTTP service (Option
B). Whoever runs the tool may be a person, a cron job or a scheduler such as Airflow. A script
cannot read an error message; it can only read the exit code, and it needs to know what to do next:
try again later, fix the configuration, run an earlier step, or report a bug.

## Decision

- **Option A: the `bis-prates` command line**, with `fetch`, `transform` and `report`, as the
  brief recommends. The HTTP service comes as Extension 1 ([0003](0003-web-extension-stack.md)) and
  calls the same code.
- **A thin CLI.** The `cli` package parses the arguments, calls the provider, `compute_snapshots`
  and `Report`, prints the result and returns a code. It holds no logic of its own, so the HTTP API
  can call the same pieces. There is no separate `services` package yet: with one interface it would
  only pass calls through. It is added with the API, if the two interfaces need shared steps.
- **Typed errors in `core`.** `core/errors.py` defines `PolicyRateMonitorError` and its
  subclasses `ConfigError`, `MissingInputError`, `SourceUnavailableError` and
  `UnexpectedFormatError`. They know nothing about the CLI: `cli/exit_codes.py` maps them to exit
  codes, and the API will map them to HTTP statuses.
- **Our own small exit codes,** a documented `ExitCode` enum, each meaning one action:

  | Code | Name | What the caller does |
  |---|---|---|
  | 0 | `OK` | nothing |
  | 1 | `NO_RESULT` | the command ran, but no requested area has a result: check the codes |
  | 2 | `USAGE_ERROR` | fix the command line (argparse's own code) |
  | 3 | `INTERNAL_ERROR` | report a bug |
  | 4 | `CONFIG_ERROR` | fix the configuration |
  | 5 | `MISSING_INPUT` | run the earlier step |
  | 6 | `SOURCE_UNAVAILABLE` | try again later; the only transient failure |
  | 7 | `UNEXPECTED_FORMAT` | look at the source: its format changed |
  | 8 | `OUTPUT_ERROR` | a file cannot be read or written: check the data and output folders |

  0, 1 and 2 mean what they mean in ruff and mypy, and 3 is pytest's internal error. Every code is
  below 126, which the shell reserves. Any unexpected exception becomes 3, with a one-line message
  and its traceback in the log; `--debug` adds the traceback for the expected errors too.

## How it evolved

The first proposal was the BSD `sysexits.h` codes (64–78). They were questioned and dropped: the
interface is deprecated by its own documentation ("retained only for compatibility. Its use is
discouraged"), it is not portable, Python's `os.EX_*` exist on Unix only, and argparse's usage
error is 2, so it would need overriding. Only 0, 1 and 2 were considered next, and rejected because
a scheduler could not tell "try again" from "fix the configuration". The first version of the chosen
scheme gave 1 to internal errors, contrary to how Python tools use 1; it was revised to the table
above.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Option B, an HTTP service, as the main interface | The brief recommends A; B comes as Extension 1 |
| A single `cli.py` | Mixes parsing, output and the exit-code contract, which could not be tested on its own |
| `typer` or `click` | Three commands do not need a dependency |
| `sysexits.h` (64–78) | Deprecated, not portable, and needs an override of argparse's error |
| Only 0, 1 and 2 | A scheduler cannot tell a transient failure from a configuration error |
| Exit codes carried by the exceptions | Would tie `core` to the CLI |

## Consequences

- A script decides what to do from the number alone; the README lists the codes.
- Only a crash before `main()` runs (a broken install) gives Python's own 1.
- The configuration commands (`config validate`, `config schema`) and the settings read from
  environment variables are added in #7 (see Updates).

## Evidence

- `src/policy_rate_monitor/cli/` (`main.py`, `exit_codes.py`), `src/policy_rate_monitor/core/errors.py`.
- `tests/test_cli.py`: `test_an_error_maps_to_its_exit_code`,
  `test_a_bug_ends_with_the_internal_error_code`,
  `test_report_before_transform_gives_a_one_line_error`,
  `test_a_bad_configuration_says_where_its_path_came_from`; `tests/core/test_errors.py`;
  `tests/test_settings.py` (the order of the layers).

## Updates

- 2026-11-20 (#7): the configuration file and the data directory are also read from
  `BIS_PRATES_CONFIG` and `BIS_PRATES_DATA_DIR`, in the order flag, variable, configuration or
  default, so a deployment such as the HTTP API is set up without a command line and reads the
  same files as the CLI. The order lives in `settings`, outside `cli`, for the API to import. An
  empty variable counts as unset. `config validate` checks the file the other commands would load
  (no positional path, which would be a second way to name it); `config schema` replaces the
  `python -m` entry that regenerated the schema. Not taken: `pydantic-settings` (two variables
  need no dependency) and a `--source` override for `fetch` (neither the API nor the tests need
  it; a configuration can name another URL).
- 2026-11-22: the shared steps came with the HTTP API (#27), as the `service` module rather
  than a `services` package ([0014](0014-http-api.md)); the CLI calls it for `report`.
- 2026-11-22 (#34): bad source data (a corrupt ZIP, a CSV that cannot be parsed or decoded or has
  no row of the configured selection, a series row that does not make valid metadata) and an HTTP
  answer that is not temporary end with 7; only a connection error, a timeout, a 5xx, 408 or 429
  is retried before 6. Staged data built with an older configuration ends with 5, and a `--port`
  outside 1 to 65535 or one that cannot be bound with 2.
  `MissingInputError` names the step to run, and each adapter words the hint its own way.
  `test_report_before_transform_gives_a_one_line_error` is now
  `test_a_missing_input_gives_a_one_line_error_naming_the_step`
  ([0017](0017-bad-input-failures-and-writes.md)).
