# 0014 The HTTP API: one service for both interfaces, jobs one at a time, errors as kinds

- **Status:** Accepted
- **Decided:** 2026-11-21 (#27)

## Context

The brief's Option B is an HTTP service, and Extension 1 a web front end over it.
[0003](0003-web-extension-stack.md) decided the stack (FastAPI, a React front end, optional
extras) and that the API calls the same code as the CLI. [0010](0010-cli-and-exit-codes.md)
left a `services` package for the day the two interfaces needed shared steps. They do now: reading
the staged data, the summary, the time series of one country or economic area and the report
are the same for both. If each adapter wrote its own, the numbers could drift apart.

The pipeline steps are slow (a download, a transform), write the same files, and one failing must
not take the service down.

## Decision

- **A `service` module shared by `cli` and `web`.** It holds `load_staged`, `ref_areas`,
  `summary`, `timeseries` and `write_report`. The adapters keep their own concerns: the CLI its
  arguments, printing and exit codes; the API its routes, statuses and jobs. `GET /summary`
  returns the document of `summary.json`, built by the same `summary_document`, so the two match
  on the same data.
- **Routes.** `GET /ref-areas`, `/summary?countries=`, `/timeseries?country=&start=`,
  `/chart?countries=&start=`, and `POST /jobs/fetch`, `/jobs/transform`, `/jobs/report` with
  `GET /jobs/{id}`. The paths and fields say `ref-areas` and `ref_area`, as
  [0004](0004-domain-model.md) names the concept; query parameters say `countries` and `country`,
  as the CLI's `--countries`. Rates are JSON numbers, as in `summary.json`. The chart is a Plotly
  figure in JSON, from the same series and rows as the PNG (`charted_series`, `valid_rows` in
  `report`).
- **Jobs on one worker thread, one at a time, in memory.** `POST` answers `202` with a `Location`
  header; the job goes `queued`, `running`, then `succeeded` (with a `result`) or `failed` (with an
  `error`). A `POST` while a job is queued or running gets `409` `job_running`. The jobs write the
  same files, so running two at once could corrupt them; one worker is the simplest guarantee.
  Jobs live in a dict behind a condition variable.
- **Errors as kinds and statuses.** Every error is `{"error": kind, "message": ...}`, with no
  traceback, and a failed job carries the same body. The kinds mirror the exit codes:
  `missing_input` 409, `source_unavailable` 503, `unexpected_format`, `file_error`,
  `config_error` and `internal_error` 500, plus `not_found` 404, `invalid_request` 422,
  `job_running` 409, `shutting_down` 503 and `http_error` (a wrong method: 405, with `Allow`).
  A blank `country` or `countries` is an `invalid_request` too (422). The routers declare these
  responses with the error body, so `/docs` shows the real one. The typed errors of `core` map to
  kinds in `web/errors.py`, as `cli/exit_codes.py` maps them to codes. A bug is logged with its traceback and the body names
  only its type.
- **The staged data is read once, kept in memory, and replaced after a transform.** The read
  endpoints do not touch the disk after the first call. A `transform` job replaces the data
  under a lock once the new files are written, so the endpoints serve it without a restart, and a
  read during the transform gets the previous data. With nothing read yet, a read during a
  transform does not wait for the lock: it gets `409` `missing_input` at once. The analytics arrays
  are not cached: `summary` builds them for the requested series on each request, which takes
  67 ms for five codes and 220 ms for all 49 on the BIS file, after a first read of the staged
  data of 0.35 s. Caching them is left until a front end needs faster answers.
- **Stopping waits for the running job.** The lifespan joins the worker off the event loop, so a
  transform never stops halfway through its files; a `POST` meanwhile gets `503`
  `shutting_down`.
- **`bis-prates serve [--host] [--port] [--out]`**, with the configuration and data directory
  found as for every command ([0010](0010-cli-and-exit-codes.md), Updates). `cli` imports `web`
  only inside the command; if FastAPI, Plotly, Starlette or uvicorn is missing, it prints a
  message naming the `web` extra and ends with code 2. `create_app(config, provider, out_dir)`
  takes the provider, so the tests run the API on the in-memory one.
- **Plain (sync) routes, run in FastAPI's thread pool, with the shared state behind locks.**
  Every route is a `def`, not an `async def`: what it calls is blocking work (pandas, Arrow, file
  reads, the snapshot search), and FastAPI runs a `def` route on its thread pool, so a slow
  request never blocks the event loop. An `async def` route would run that work on the loop itself
  and stall every other request. The state shared between threads is guarded:
  - the jobs behind one `threading.Condition` (`JobRunner`), which also lets a caller wait for a
    job;
  - the staged data behind a `threading.Lock`, plus an `Event` that marks a running transform
    (`_Staged`);
  - `StagedData` is replaced, never changed in place, and the routes only read it (pandas reads
    from several threads are safe; writes would not be).

  The only `async` code is the lifespan, which hands the blocking join of the worker to a thread
  (`asyncio.to_thread`). So the tests need no `pytest-asyncio`: FastAPI's `TestClient` drives the
  app from ordinary test functions, and the job tests use `threading.Event`s, not sleeps, to hold
  a job at a known point.
- **Extras.** `interactive` is Plotly; `web` is FastAPI, Starlette, uvicorn and `interactive`
  (the package lists itself, which deptry is told to accept). Starlette is listed although FastAPI
  installs it, because `web/errors.py` imports it directly: an unknown route or a wrong method
  raises Starlette's `HTTPException`, not FastAPI's subclass, so the handler that gives those the
  error body must register Starlette's class. deptry rejects a direct import of a package that
  only arrives through another one (DEP003): if FastAPI ever stopped depending on it, the import
  would break. The test group has `httpx2`, the HTTP client FastAPI's `TestClient` runs on:
  Starlette 1.10 deprecates the older `httpx` for it and warns when it falls back to `httpx`.
  `charts` and `web` are modules in `tach.toml`; `charts` depends on `core` and `report`, `web` on `service`, `charts`, `report`,
  `providers`, `config` and `core`. Plotly ships no type stubs, so `stubs/plotly/` types the
  few names used.
- **No CORS headers.** The Vite development server of the front end forwards its calls (M8), so
  the browser talks to one origin.

## Alternatives considered

| Alternative | Why not |
|---|---|
| FastAPI `BackgroundTasks` | No status to poll, no id, and nothing refuses a second job while one runs |
| A task queue (Celery, RQ) or a job table on disk | A broker or a database for three jobs of one tool; jobs that survive a restart are not needed, as the files they write are the result |
| The API importing from the CLI | Ties the API to argument parsing, printing and exit codes |
| The API copying the CLI's steps | Two copies that can drift: the numbers would differ |
| CORS in the API | Opens the API to other origins to solve what the development server's proxy solves; revisit if the front end is served from another origin |
| Serving the files of `out/` over HTTP | A second way to read the summary, with its own format; `/summary` and `/chart` return data, not files |
| Authentication | The service is for one team on a trusted network, behind a proxy that can check who calls; adds nothing to the brief |
| Several uvicorn workers | Each would have its own jobs and cache; see Consequences |
| `async def` routes | The work is blocking (pandas, files), so it would block the event loop; making it truly async would need async file and data libraries for no gain at this size |
| `pytest-asyncio` and an async test client | Nothing to await in the routes; `TestClient` covers them synchronously |
| `httpx` in the test group | Deprecated by Starlette 1.10 for its test client, with a warning on every run |

## Consequences

- Jobs are lost when the service restarts, with their ids. The files they wrote stay.
- One process only: several uvicorn workers would each have their own jobs, so "one at a time" would
  not hold, and each their own copy of the data.
- A read during a transform gets the previous data, and a read after it the new one; during the
  first transform, a read is told at once that nothing is staged yet.
- Ctrl+C waits for a running job, which can take minutes for a slow download; killing the
  process stops it at once, as `fetch` never replaces the cached file with a partial one.
- The CLI and the API give the same numbers on the same data, because they share `service`.
- The API has no authentication and no CORS: it listens on `127.0.0.1` by default.
- A failure in a job never shows a traceback to the caller, only in the log.

## Evidence

- `src/policy_rate_monitor/service.py`, `charts.py`, `web/app.py`, `web/jobs.py`,
  `web/errors.py`; `cli/main.py` (`serve`); `tach.toml`; `pyproject.toml` (the extras).
- `tests/web/test_app.py`, `tests/web/test_jobs.py`, `tests/test_charts.py`,
  `tests/test_service.py`.

## Updates

- 2026-11-22: the modules named above have moved. `charted_series` and `valid_rows` are in
  `chart.lines`, and `rate_figure` in `chart.interactive`; `charts.py` is now the `chart`
  package (`src/policy_rate_monitor/chart/`), which tach lets depend on `core` only, with
  `report` depending on `chart` ([0016](0016-accessible-charts.md)).
  `tests/test_charts.py` is now `tests/chart/test_interactive.py`.
- 2026-11-22 (#34): the job runner keeps the last 100 finished jobs (`KEEP_FINISHED`) and marks a
  job failed whatever ends it. Requested codes that do not match the configuration's code pattern
  get `422` `invalid_request`, and a `missing_input` message ends with the job to run,
  `POST /jobs/<step>`. `serve` binds its socket before uvicorn starts, so a busy port ends with
  exit code 2 ([0017](0017-bad-input-failures-and-writes.md)).
