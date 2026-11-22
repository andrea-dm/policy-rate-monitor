# HTTP API

The same steps as the command line, over HTTP: the available countries and economic areas, the
summary, the time series of one of them, the chart, and `fetch`, `transform` and `report` as
background jobs. The reasons for the design are in [decision 0014](adr/0014-http-api.md).

## Install and start

The API needs the `web` extra (FastAPI, Starlette, uvicorn, and Plotly for the chart). A plain
install does not have it.

```bash
uv sync --extra web                      # from a clone
pip install 'policy-rate-monitor[web]'   # from a package
```

Start it:

```bash
bis-prates serve                              # http://127.0.0.1:8000
bis-prates serve --host 0.0.0.0 --port 9000 --out results
```

| Option | Default | Meaning |
|---|---|---|
| `--host ADDRESS` | `127.0.0.1` | the address to listen on |
| `--port N` | `8000` | the port to listen on, from 1 to 65535 |
| `--out DIR` | `out` | where a `report` job writes its files |
| `--config PATH` | `$BIS_PRATES_CONFIG`, else `configs/bis_cbpol.json` | the dataset configuration |
| `--data-dir PATH` | `$BIS_PRATES_DATA_DIR`, else `dataset.data_dir` in the configuration | where `raw/` and `staged/` are |

The configuration and the data directory are found as for every command: the flag, then the
environment variable, then the default ([CLI reference](cli.md#where-the-options-come-from)).
If the address and port cannot be used (the port is already in use, say), `serve` prints a message
naming them and ends with exit code 2.

FastAPI's interactive page is at <http://127.0.0.1:8000/docs>: it lists every endpoint with its
parameters, its responses and the [error body](#errors), and sends requests from the browser. The
OpenAPI document is at `/openapi.json`. `/summary` is described there only as an object: its
fields are those of `summary.json`, below.

The read endpoints need the staged data. On a new install, run the steps first, with the commands or
with the [jobs](#jobs):

```bash
bis-prates fetch && bis-prates transform
```

## Endpoints

| Method and path | Parameters | What it returns |
|---|---|---|
| `GET /ref-areas` | - | the countries and economic areas the data has a series for |
| `GET /summary` | `countries` (required, comma-separated) | the latest rate, the rate 12 months earlier and the change, per code |
| `GET /timeseries` | `country` (required), `start` | the valid observations of one country or economic area |
| `GET /chart` | `countries` (required, comma-separated), `start` | the policy-rate chart, as a Plotly figure |
| `POST /jobs/fetch` | - | `202` and a job: download the source file |
| `POST /jobs/transform` | - | `202` and a job: stage the downloaded file |
| `POST /jobs/report` | body: `countries`, `start` | `202` and a job: write the report files |
| `GET /jobs/{id}` | - | a job's status, with its result or its error |

Codes are matched as in the CLI: without regard to case and spaces, and aliases such as `EA` for
`XM` are applied. A blank code (`country=`, or `countries=` with only commas and spaces) gets
`422`, and so does a code with other characters than letters, digits and `_ @ $ -`, the code
pattern of the configuration, so no spreadsheet function, link or HTML can reach the outputs (a
leading `-` or `@` is allowed, as in the configuration's codes). `start` is a date,
`YYYY-MM-DD`; without it the whole history is used. Every failure has the [same body](#errors).

### `GET /ref-areas`

```bash
curl http://127.0.0.1:8000/ref-areas
```

```json
[
  {"ref_area": "AR", "ref_area_name": "Argentina", "aliases": []},
  {"ref_area": "XM", "ref_area_name": "Euro area", "aliases": ["EA"]}
]
```

### `GET /summary`

```bash
curl 'http://127.0.0.1:8000/summary?countries=US,EA'
```

It returns the same document as `out/summary.json` (see [Data model](data-model.md)): `generated_at`,
`source`, `dataset` and `snapshots`, where `generated_at` is the time of the request and `source`
the release the staged data was built from (`null` when it is unknown). A code the data does not
have is not an error: its snapshot has the status `unknown_ref_area`.

```json
{
  "generated_at": "2026-11-21T16:28:20+00:00",
  "source": {
    "location": "https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip",
    "version_id": null,
    "checksum_sha256": "79f614ab22c21d172694cd48e9a9d551c7196f53c525008bc7ea4da8831d6494",
    "retrieved_at": "2026-11-19T22:44:41.308601Z"
  },
  "dataset": {"id": "bis_cbpol", "title": "Central bank policy rates"},
  "snapshots": [
    {
      "requested_ref_area": "EA",
      "ref_area": "XM",
      "series": {"series_key": "D.XM", "ref_area": "XM", "ref_area_name": "Euro area", "...": "..."},
      "latest_date": "2026-11-17",
      "latest_value": 2.5,
      "target_date": "2025-11-17",
      "prior_date": "2025-11-17",
      "prior_value": 2.0,
      "change_bps": 50.0,
      "status": "ok",
      "flags": []
    }
  ]
}
```

Rates are JSON numbers, not strings ([decision 0004](adr/0004-domain-model.md)).

The analytics arrays are not cached: each request builds them for the requested codes, which took
67 ms for five codes and 220 ms for all 49 on the BIS file ([decision 0014](adr/0014-http-api.md)).

### `GET /timeseries`

```bash
curl 'http://127.0.0.1:8000/timeseries?country=EA&start=2025-11-14'
```

```json
{
  "requested_ref_area": "EA",
  "series": {"series_key": "D.XM", "ref_area": "XM", "ref_area_name": "Euro area", "...": "..."},
  "observations": [
    {"date": "2025-11-14", "value": 2.0},
    {"date": "2025-11-15", "value": 2.0}
  ]
}
```

Only valid observations are listed, oldest first. A code the data does not have gives `404` with
the kind `not_found`.

### `GET /chart`

```bash
curl 'http://127.0.0.1:8000/chart?countries=US,EA&start=2015-01-01'
```

It returns a [Plotly](https://plotly.com/javascript/) figure in JSON, with one step line per
charted series that has a valid observation from `start` on, in the order requested: the same series and rows as `out/policy_rates.png`. A
browser draws it with `Plotly.newPlot(div, figure.data, figure.layout)`.

The n-th line has the same colour and dash as in the PNG, from the colour-blind palette of
[decision 0016](adr/0016-accessible-charts.md). Each line is named at its right end by an
annotation, the legend is below the plot, and the hover shows the date and the rate with the
series' decimals. `layout.meta.description` holds the chart's text alternative, for the page that
shows it; the legend and the text alternative name only the lines drawn. With nothing to chart, the figure has no lines and one annotation, "No data to plot".

```json
{
  "data": [
    {"name": "United States", "mode": "lines", "line": {"color": "#006BA4", "dash": "solid", "shape": "hv", "width": 2}, "hovertemplate": "%{x|%Y-%m-%d}: %{y:.4f}%", "x": ["2015-01-01", "..."], "y": [0.125, "..."], "type": "scatter"},
    {"name": "Euro area", "mode": "lines", "line": {"color": "#C85200", "dash": "solid", "shape": "hv", "width": 2}, "hovertemplate": "%{x|%Y-%m-%d}: %{y:.4f}%", "x": ["2015-01-01", "..."], "y": [0.05, "..."], "type": "scatter"}
  ],
  "layout": {
    "title": {"text": "Central bank policy rates"},
    "yaxis": {"title": {"text": "Per cent"}},
    "annotations": [
      {"text": "United States", "x": "2026-11-17", "y": 3.875, "xanchor": "left", "xshift": 6, "showarrow": false, "font": {"color": "#006BA4"}},
      {"text": "Euro area", "x": "2026-11-17", "y": 2.5, "xanchor": "left", "xshift": 6, "showarrow": false, "font": {"color": "#C85200"}}
    ],
    "legend": {"orientation": "h", "yanchor": "top", "y": -0.12},
    "meta": {"description": "Central bank policy rates: a step chart of United States and Euro area, in per cent, from 2015-01-01."},
    "...": "..."
  }
}
```

The chart is drawn from floating-point values; the numbers of the other endpoints stay exact.

## Jobs

`fetch`, `transform` and `report` can take seconds or minutes, so a `POST` does not wait for them.
It answers `202 Accepted` with the job and, in the `Location` header, the address to poll:

```bash
curl -i -X POST http://127.0.0.1:8000/jobs/fetch
```

```text
HTTP/1.1 202 Accepted
location: /jobs/6f0c1b0a5e0d4e0c9c1d2f3a4b5c6d7e
```

```json
{
  "id": "6f0c1b0a5e0d4e0c9c1d2f3a4b5c6d7e",
  "kind": "fetch",
  "status": "queued",
  "created_at": "2026-11-21T16:30:00.120345Z",
  "started_at": null,
  "finished_at": null,
  "result": null,
  "error": null
}
```

Poll the job until its status is `succeeded` or `failed`:

```bash
curl http://127.0.0.1:8000/jobs/6f0c1b0a5e0d4e0c9c1d2f3a4b5c6d7e
```

| Status | Meaning |
|---|---|
| `queued` | accepted, not started |
| `running` | the worker is on it |
| `succeeded` | done; `result` holds what it produced |
| `failed` | stopped; `error` holds [why](#errors) |

| Job | `result` when it succeeded |
|---|---|
| `fetch` | `{"release": {"location": ..., "version_id": ..., "checksum_sha256": ..., "retrieved_at": ...}}` |
| `transform` | `{"observations": 709984, "series": 49, "findings": 0}` |
| `report` | `{"files": ["out/summary.csv", "out/summary.json", "out/policy_rates.png", "out/report.md", "out/report.html"]}` |

A `report` job takes a body and writes the files into the `--out` folder, as `bis-prates report`
does:

```bash
curl -X POST http://127.0.0.1:8000/jobs/report \
  -H 'Content-Type: application/json' \
  -d '{"countries": ["US", "EA"], "start": "2015-01-01"}'
```

`countries` is required and cannot be empty; `start` is optional. A bad body is refused at once with
`422`.

The rules:

- **One job at a time.** The jobs write the same files, so one worker thread runs them. A `POST`
  while a job is queued or running gets `409` with the kind `job_running`, naming the job to wait
  for.
- **Jobs are kept in memory.** They are lost when the service stops, and their ids with them. The
  files they wrote stay. Only the last 100 finished jobs are kept: an older id gets `404`
  `not_found`. A queued or running job is always kept.
- **A job always ends.** Whatever stops it, even an error that is not a Python `Exception`, the
  job is marked `failed`, so the next `POST` is accepted.
- **A transform refreshes the read endpoints.** The staged data is read once and kept in memory. A
  `transform` job replaces it once the new files are written, so the endpoints serve the new data
  without a restart. A read during the transform gets the previous data. On a new install there is
  none: a read during the first transform gets `409` `missing_input` at once, saying a transform
  is running; poll the job, then read again.
- **Stopping waits for the running job.** Ctrl+C stops accepting requests, then waits for a
  running job to finish, so a transform never stops halfway through writing its files. A `POST`
  while the service is stopping gets `503` `shutting_down`. To stop at once, kill the process.
- **One process.** Run one uvicorn process: several would each have their own jobs and their own
  copy of the data.

A script that fetches and stages the data, and stops at the first failed job:

```bash
api=http://127.0.0.1:8000
wait_for() {  # wait_for JOB_PATH; fails if the job failed
  until job=$(curl -s "$api$1") && echo "$job" | grep -Eq '"status": ?"(succeeded|failed)"'; do
    sleep 1
  done
  echo "$job"
  ! echo "$job" | grep -Eq '"status": ?"failed"'
}
for step in fetch transform; do
  path=$(curl -si -X POST "$api/jobs/$step" | tr -d '\r' | sed -n 's/^location: //Ip')
  wait_for "$path" || exit 1
done
```

## Errors

Every error response has the same body, and never a traceback:

```json
{"error": "not_found", "message": "no series for UK; GET /ref-areas lists the codes"}
```

`error` is the kind, for a program to branch on; `message` is for a person. The kinds mirror the
CLI's [exit codes](cli.md#exit-codes):

| Kind | Status | When | CLI exit code |
|---|---|---|---|
| `missing_input` | 409 | nothing is staged yet, or the staged data was built with another configuration: the message names the job to run | 5 |
| `not_found` | 404 | no series for the code, no job with the id, or an unknown path | - |
| `invalid_request` | 422 | a missing or malformed parameter or body, a blank or empty code, or a code outside the code pattern | 2 |
| `job_running` | 409 | a job is queued or running; wait for it | - |
| `shutting_down` | 503 | the service is stopping and accepts no new job | - |
| `http_error` | other | any other HTTP error, e.g. `405` with an `Allow` header | - |
| `source_unavailable` | 503 | the BIS site cannot be reached and nothing is cached: try again later | 6 |
| `unexpected_format` | 500 | the source file is not what the configuration describes, or the BIS site answers with a status such as 404 | 7 |
| `file_error` | 500 | a file cannot be read or written | 8 |
| `config_error` | 500 | the configuration is invalid | 4 |
| `internal_error` | 500 | a bug; the message is only the name of its type, and the traceback goes to the log | 3 |

An `http_error` keeps its own status: a wrong method on a path gets `405`, with the methods it
accepts in the `Allow` header.

A job that fails ends with the status `failed`, and its `error` holds the same body:

```json
{
  "id": "6f0c1b0a5e0d4e0c9c1d2f3a4b5c6d7e",
  "kind": "report",
  "status": "failed",
  "created_at": "2026-11-21T16:31:00.120345Z",
  "started_at": "2026-11-21T16:31:00.130012Z",
  "finished_at": "2026-11-21T16:31:00.131870Z",
  "result": null,
  "error": {
    "error": "missing_input",
    "message": "data/staged/observations.parquet does not exist: run POST /jobs/transform first"
  }
}
```

The `GET /jobs/{id}` answer itself is `200`: the failure is the job's, not the request's. A
`missing_input` message ends with the job to run first, `POST /jobs/fetch` or
`POST /jobs/transform`; the command line names the same step as `bis-prates transform`.

## Same numbers as the CLI

`bis-prates report` and the API both call the `service` module
([architecture](architecture.md#the-modules-and-their-layers)), so on the same data `GET /summary`
returns the document `report` writes to `summary.json`, apart from `generated_at`.

## The front end and CORS

The API sends no CORS headers. The web front end ([decision 0003](adr/0003-web-extension-stack.md),
M8, not built yet) will be served by the Vite development server, which will forward its calls to
the API, so the browser talks to one origin only. A page served from another origin cannot call
the API directly.

The API has no authentication: listen on `127.0.0.1` (the default), or put it behind a proxy that
checks who calls.

## See also

[`examples/http_api.ipynb`](https://github.com/andrea-dm/policy-rate-monitor/blob/develop/examples/http_api.ipynb)
calls every endpoint from Python, without importing the package: start `bis-prates serve`, then
run it.

[`policy_rate_monitor.web.app`](reference.md#policy_rate_monitor.web.app),
[`policy_rate_monitor.web.jobs`](reference.md#policy_rate_monitor.web.jobs),
[`policy_rate_monitor.web.errors`](reference.md#policy_rate_monitor.web.errors) and
[`policy_rate_monitor.service`](reference.md#policy_rate_monitor.service) in the API reference.
