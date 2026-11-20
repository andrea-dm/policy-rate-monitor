# CLI reference

`bis-prates` has three commands, run in this order. Each one writes files the next one reads, so a
step can be rerun on its own. `python -m policy_rate_monitor.cli` works the same way, without the
script on the `PATH`.

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
old. When the site is down, an older copy is used with a warning. Step by step: [How it
works](how-it-works.md#1-get-the-latest-data-11-the-ingestion-process).

Writes `data/raw/WS_CBPOL_csv_flat.zip` and `data/raw/WS_CBPOL_csv_flat.json`, and prints the
address and the SHA-256 fingerprint of the release:

```text
https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip (sha256 79f614ab22c2…)
```

### `bis-prates transform`

Loads the cached CSV, as it is, into `data/raw/WS_CBPOL_csv_flat.parquet`, then stages the daily
series: `data/staged/observations.parquet`, `series.json` and `data_quality.json`. Prints the
number of observations and series kept and the number of data-quality findings, for example:

```text
709984 observations of 49 series kept, 0 data-quality findings
```

### `bis-prates report`

For each requested area, finds the latest valid rate, the rate 12 months earlier and the change in
basis points, and writes `out/summary.csv`, `out/summary.json`, `out/policy_rates.png` and
`out/report.md`. Prints the paths of the four files. For a code the data does not have, it prints a
warning listing every code the data has, with its name:

```text
warning: no series for UK. Available codes: AR (Argentina), AT (Austria), … ; EA stands for XM.
```

## Options

Options go after the command (`bis-prates report --out results`).

| Option | Commands | Default | Meaning |
|---|---|---|---|
| `--config PATH` | all | `configs/bis_cbpol.json` | the dataset configuration ([Configuration](configuration.md)) |
| `--data-dir PATH` | all | `dataset.data_dir` in the configuration (`data`) | where `raw/` and `staged/` go; a relative path is relative to the current folder |
| `--debug` | all | off | show the full traceback of an error |
| `--countries "US,EA"` | `report` | required | the area codes, separated by commas; trimmed, upper-cased and de-duplicated; aliases such as `EA → XM` applied |
| `--start YYYY-MM-DD` | `report` | the whole history | the first date of the chart; the summary is not affected |
| `--out DIR` | `report` | `out` | the output folder |

## Exit codes

Each kind of failure has its own code, so a script or a scheduler can decide what to do from the
number alone ([decision 0010](adr/0010-cli-and-exit-codes.md)).

| Code | Name | Meaning | What to do |
|---|---|---|---|
| 0 | `OK` | success | - |
| 1 | `NO_RESULT` | no requested area has a complete result (status `ok`); the outputs are still written | check the area codes and the statuses in the summary |
| 2 | `USAGE_ERROR` | invalid arguments | fix the command line |
| 3 | `INTERNAL_ERROR` | an unexpected error, that is a bug; the traceback is logged | report it |
| 4 | `CONFIG_ERROR` | the configuration is missing or invalid | fix the configuration file |
| 5 | `MISSING_INPUT` | a step's input is missing | run the previous step (`transform` before `report`) |
| 6 | `SOURCE_UNAVAILABLE` | the source cannot be reached and nothing is cached | try again later |
| 7 | `UNEXPECTED_FORMAT` | the source file is not what the configuration describes | check the BIS file; the configuration may need updating |
| 8 | `OUTPUT_ERROR` | the outputs cannot be written | check the output folder |

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
