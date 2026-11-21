# CLI reference

`bis-prates` has three pipeline commands, run in this order. Each one writes files the next one
reads, so a step can be rerun on its own. `serve` runs the [HTTP API](api.md), and two more
commands check the configuration and print its format. `python -m policy_rate_monitor.cli` works the same way, without the script on the `PATH`.

```bash
bis-prates fetch
bis-prates transform
bis-prates report --countries "US,EA,GB,JP,CH" --start 2015-01-01
```

Results go to standard output; warnings, errors and the log go to standard error.

## Commands

### `bis-prates fetch`

Finds the "Central bank policy rates (CSV, flat)" ZIP on the BIS bulk-download page, downloads it,
checks it, and stores it with its release record, unless the cached copy is less than 24 hours
old (`cache_ttl_hours` in the configuration). When the site is down, an older copy is used with a
warning. Step by step: [How it works](how-it-works.md#1-get-the-latest-data-11-the-ingestion-process).

Writes `data/raw/WS_CBPOL_csv_flat.zip` and `data/raw/WS_CBPOL_csv_flat.json`, and prints the
address and the SHA-256 fingerprint of the release (example from the release of 19 November
2026):

```text
https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip (sha256 79f614ab22c2…)
```

### `bis-prates transform`

Loads the cached CSV, as it is, into `data/raw/WS_CBPOL_csv_flat.parquet`, then stages the daily
series: `data/staged/observations.parquet`, `series.json` and `data_quality.json`. Prints the
number of observations and series kept and the number of data-quality findings, for example, for
the release of 19 November 2026:

```text
709984 observations of 49 series kept, 0 data-quality findings
```

### `bis-prates report`

For each requested country or economic area, finds the latest valid rate, the rate 12 months earlier and the change in
basis points, and writes `out/summary.csv`, `out/summary.json`, `out/policy_rates.png`,
`out/report.md` and `out/report.html`. Prints the paths of the five files. `report.html` has the
interactive chart with the `interactive` extra, and the PNG without it; it works offline either
way ([decision 0015](adr/0015-report-templates-and-html.md)). For a code the data does not have, it prints a
warning listing every code the data has, with its name:

```text
warning: no series for UK. Available codes: AR (Argentina), AT (Austria), … ; EA stands for XM.
```

### `bis-prates serve`

Runs the [HTTP API](api.md) until it is stopped. Ctrl+C stops accepting requests and waits for a
running job to finish ([Jobs](api.md#jobs)); kill the process to stop at once. It reads the
configuration and the data directory as the other commands do; the staged data is read when the
first request needs it.

```bash
bis-prates serve                              # http://127.0.0.1:8000, docs at /docs
bis-prates serve --host 0.0.0.0 --port 9000 --out results
```

It needs the `web` extra. Without it, the command prints a message naming the extra and ends with
code 2:

```text
error: bis-prates serve needs the web extra: install policy-rate-monitor[web]
```

A missing or invalid configuration ends with code 4, as for the other commands. `--out` is where
the API's `report` jobs write their files.

### `bis-prates config validate`

Loads the configuration the other commands would load, found the same way (see
[Where the options come from](#where-the-options-come-from)), and prints its path and where the
path came from:

```text
configs/bis_cbpol.json: valid (the default)
```

A missing or invalid configuration gives the same message as the other commands, and exit code 4.

### `bis-prates config schema`

Prints the JSON Schema of the configuration ([Configuration](configuration.md)). It needs no
configuration file. After a change to the configuration models, regenerate the committed schema
with it:

```bash
uv run bis-prates config schema > configs/config.schema.json
```

## Options

Options go after the command (`bis-prates report --out results`). `bis-prates --version` prints the
version.

| Option | Commands | Default | Meaning |
|---|---|---|---|
| `--config PATH` | all but `config schema` | `$BIS_PRATES_CONFIG`, else `configs/bis_cbpol.json` | the dataset configuration ([Configuration](configuration.md)) |
| `--data-dir PATH` | `fetch`, `transform`, `report`, `serve` | `$BIS_PRATES_DATA_DIR`, else `dataset.data_dir` in the configuration (`data`) | where `raw/` and `staged/` go; a relative path is relative to the current folder |
| `--debug` | all | off | show the full traceback of an error |
| `--countries "US,EA"` | `report` | required | the codes, separated by commas; matched without regard to case and spaces, de-duplicated, and written as typed next to the code used; aliases such as `EA → XM` applied |
| `--start YYYY-MM-DD` | `report` | the whole history | the first date of the chart; the summary is not affected |
| `--out DIR` | `report`, `serve` | `out` | the output folder; for `serve`, where the `report` jobs write |
| `--host ADDRESS` | `serve` | `127.0.0.1` | the address the HTTP API listens on |
| `--port N` | `serve` | `8000` | the port the HTTP API listens on |

## Where the options come from

The configuration file and the data directory are each taken from the first place that gives one:

1. the option on the command line;
2. the environment variable, `BIS_PRATES_CONFIG` or `BIS_PRATES_DATA_DIR`; an empty variable
   counts as unset;
3. for the data directory, `dataset.data_dir` in the configuration; for the configuration file,
   `configs/bis_cbpol.json`.

The variables set up a whole deployment, for example a scheduled job or the HTTP API, which reads
its files the same way:

```bash
export BIS_PRATES_CONFIG=/etc/bis-prates/config.json
export BIS_PRATES_DATA_DIR=/var/lib/bis-prates
bis-prates fetch && bis-prates transform
```

When the configuration cannot be loaded, the message ends with the path and where it came from, so
the wrong file is easy to spot:

```text
error: configs/bis_cbpol.json: cannot read the configuration: No such file or directory
  configs/bis_cbpol.json: the default; choose another file with --config or BIS_PRATES_CONFIG
```

## Exit codes

Each kind of failure has its own code, so a script or a scheduler can decide what to do from the
number alone ([decision 0010](adr/0010-cli-and-exit-codes.md)).

| Code | Name | Meaning | What to do |
|---|---|---|---|
| 0 | `OK` | success | - |
| 1 | `NO_RESULT` | no requested country or area has a complete result (status `ok`); the outputs are still written | check the codes and the statuses in the summary |
| 2 | `USAGE_ERROR` | invalid arguments, or `serve` without the `web` extra | fix the command line, or install the extra |
| 3 | `INTERNAL_ERROR` | an unexpected error, that is a bug; the traceback is logged | report it |
| 4 | `CONFIG_ERROR` | the configuration is missing or invalid | fix the configuration file |
| 5 | `MISSING_INPUT` | a step's input is missing | run the previous step (`transform` before `report`) |
| 6 | `SOURCE_UNAVAILABLE` | the source cannot be reached and nothing is cached | try again later |
| 7 | `UNEXPECTED_FORMAT` | the source file is not what the configuration describes | check the BIS file; the configuration may need updating |
| 8 | `OUTPUT_ERROR` | a file cannot be read or written | check the data and output folders |

Only code 6 means "try again later". For example, in a nightly job:

```bash
bis-prates fetch
case $? in
  0) ;;
  6) echo "BIS unreachable, retry later" >&2; exit 75 ;;
  *) echo "fetch failed, see the log" >&2; exit 1 ;;
esac
bis-prates transform && bis-prates report --countries "US,EA,GB,JP,CH"
```

The codes are defined in `policy_rate_monitor.cli.exit_codes.ExitCode`
([API reference](reference.md)).
