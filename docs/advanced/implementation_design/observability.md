# Observability

What the system emits so that a run can be followed and a failure diagnosed, and what it does not
provide. It writes a log and returns records of its own work; it has no metrics, tracing, health
endpoint or alerting. The operational side (who watches what) belongs to the pipelines described in
[Operations](../operations/index.md).

## Logging

### Set-up

Logging is configured in one place: `cli.main.main` calls
`logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s
%(name)s: %(message)s")` after parsing the arguments. Every module uses
`logging.getLogger(__name__)`, so the logger name is the module path, for example
`policy_rate_monitor.fetch`.

- The level is INFO and no option changes it. The code has no DEBUG messages.
- `--debug` does not raise the level; it adds the traceback of a typed error or of an `OSError` to
  the log. A bug (an exception that is not raised on purpose) is always logged with its traceback.
- Results go to standard output and the log to standard error, so a script can capture one without
  the other.
- Used as a library, with no call to `main`, the package installs no handler: Python's last-resort
  handler shows warnings and above on standard error, and the caller configures the rest.

### What is logged

| Logger | Level | Message | When |
|---|---|---|---|
| `fetch` | INFO | `using the cached copy from <time>` | the cache is fresh; no request made |
| `fetch` | INFO | `downloaded <url> (<n> bytes)` | after a successful download |
| `fetch` | WARNING | `<error> (attempt <i> of <n>); retrying` | each failed attempt that is retried |
| `fetch` | WARNING | `<error>; using the fallback URL` | the discovery page cannot be read |
| `fetch` | WARNING | `no link to <file> on <page>; using the fallback URL` | no matching link |
| `fetch` | WARNING | `<error>; using the cached copy from <time>` | the source is unreachable and a copy exists |
| `fetch` | WARNING | `<path> is unreadable; the cached copy is ignored` | the release record cannot be read |
| `transform` | INFO | `data-quality rule <name> is not implemented yet` | a configured rule has no code |
| `transform` | INFO | `read <n> rows; kept <n> observations of <n> series; <n> data-quality findings` | the end of a run |
| `transform` | WARNING | `optional column <name> is absent from the source file` | an optional column is missing |
| `transform` | WARNING | `<concept> of series <key> is <text>, not a whole number of 0 or more; its decimals are unknown` | a bad `DECIMALS` |
| `transform` | WARNING | `no valid release record next to the ZIP; the release is unknown` | no usable record |
| `transform` | WARNING | `the release record is not that of the ZIP read; not staged` | the checksums differ |
| `transform` | WARNING | `<path> is unreadable; the source release is unknown` | `release.json` cannot be read |
| `cli.main` | ERROR (exception) | `unexpected error, please report it`, with the traceback | a bug |
| `cli.main` | ERROR (exception) | `the command failed`, with the traceback | a typed error or an `OSError`, with `--debug` |
| `web.app` | INFO | `listening on <host>, port <port>` | `serve` has bound its socket |
| `web.errors` | ERROR | `unexpected error, please report it`, with the traceback | a bug in a request or a job |

Under `serve`, the same handler receives the package's messages, and uvicorn applies its own default
logging configuration for its loggers (the server's start and stop lines and the access log); that
configuration does not disable the other loggers. In the installed uvicorn the access log, one line
per request, is written to standard output and the other uvicorn lines to standard error; this is
uvicorn's default, not a setting of the package.

What a log line can contain: addresses of the BIS site, file paths, series keys, codes and counts.
It carries no credential, because the system handles none ([Security](security.md)).

## Records the system produces about its own work

| Record | Where | What it shows |
|---|---|---|
| `TransformSummary` | printed by `transform` and logged | rows read, observations, series and findings kept |
| Transform job result | `GET /jobs/{id}` of a `transform` job: `observations`, `series`, `findings`, counted on the staged data read back (`web.app`, `post_transform`) | observations, series and findings kept |
| Data-quality findings | `staged/data_quality.json`, the report | per rule and series: the count and what was done ([Data quality](../../data-quality.md#7-where-the-findings-go)) |
| Source release | `staged/release.json`, `raw/<stem>.json`, `summary.json` (`source`), the report | where the data came from, its SHA-256 and when it was retrieved |
| `generated_at` | `summary.json`, the report | when the output was made |
| Job record | `GET /jobs/{id}` | `created_at`, `started_at`, `finished_at`, `status`, `result` or `error`: the only timing the system reports |
| Snapshot status and flags | `summary.csv`, `summary.json`, the report | which codes have a complete result and which are stale |

These make a result auditable: from an output file one can read which release it was built from,
how many rows were read and what was found wrong, without the log.

## Diagnosing a failure

| Symptom | Where to look |
|---|---|
| A command ended with a code other than 0 | The code names the kind ([Failure modes](failure_modes.md)); the line starting `error:` on standard error has the message; `--debug` adds the traceback |
| `fetch` succeeded but the data looks old | The INFO line `using the cached copy from <time>` (the cache was fresh, no request made) or the WARNING `<error>; using the cached copy from <time>` (the source could not be reached); `retrieved_at` in `raw/<stem>.json` |
| A report names no release | A `release record` warning in the `transform` log; `staged/release.json` is absent |
| Findings appeared | `data_quality.json`, or the report's findings section |
| A job `failed` | `GET /jobs/{id}`: its `error` has the kind and message; a bug is also in the server's log with the traceback |
| The API returns old data after a `transform` on the command line | The cache is refreshed only by a transform job or a restart ([Runtime behaviour](runtime_behavior.md#the-staged-data-cache)) |

## Not provided

- **Metrics.** There is no counter, gauge or histogram, and no metrics library in the dependencies.
- **Tracing.** There is no trace or request id, and no tracing library.
- **A health or readiness endpoint.** The API has only the routes listed in
  [Interfaces](interfaces.md#the-http-api). A process check is a request to `GET /ref-areas` (it
  answers 409 until the data is staged) or to `/docs`.
- **Structured logs.** The format is the one line above; there is no JSON log and no option to
  choose a level or a destination.
- **Alerting.** Nothing in the package sends a notification. A scheduler reacts to the exit codes.
