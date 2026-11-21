# Configuration

Everything the program knows about a dataset comes from one JSON file in `configs/`. This page
says what the two files there are, what each section of the configuration controls, which
data-quality rules exist, how to describe a second dataset, and what the schema file is for.

| File | Written by | Read by |
|---|---|---|
| `configs/bis_cbpol.json` | a person | the program, when it starts |
| `configs/config.schema.json` | a command (never edit it by hand) | your editor, and other tools |

## `bis_cbpol.json`: the configuration

The configuration of the BIS policy-rate dataset: where the file is downloaded from, which columns
it has, which series to keep, the data-quality rules and the output. The program reads it with
`FileConfigSource` and checks it against the models in `policy_rate_monitor.config.models`. A
mistake stops the program with one line per problem, for example:

```text
error: configs/bis_cbpol.json: invalid configuration (1 error)
  source.max_attempts: Input should be a valid integer (got "three")
```

### The sections

| Section | What it controls | Example from `bis_cbpol.json` |
|---|---|---|
| `dataset` | The dataset's identity: its id, the report's title, the "About the data" paragraph and the source credit, and the default data folder | `"data_dir": "data"` |
| `source` | Where and how the file is downloaded: the page searched for the link, the file name, the fallback address, the cache lifetime, the attempts and the timeouts | `"cache_ttl_hours": 24` |
| `structure` | The source's concepts and their roles: the dimensions of the series key, the time period, the measure (with its precision, scale and unit multiplier), the observation and series attributes, which attribute fills each series-metadata field, and the series to keep | `"selection": { "FREQ": "D" }` |
| `columns` | How the concepts are written in the file: encoding, delimiter, the separators of `CODE:Label` headers and `code: label` values, the texts that mean "no value", the columns that are loaded but not used, and the ones that may be absent | `"missing_values": ["NaN"]` |
| `rules` | The data-quality rules to run, each with a severity and its parameters (next section) | `{ "name": "exact_duplicate", "severity": "info" }` |
| `snapshot` | The calculation: the look-back in months and the two staleness thresholds in days | `"lookback_months": 12` |
| `output` | The series-metadata fields added to each row of `summary.csv`, in column order | `["series_key", "ref_area_name", …]` |
| `ref_area_aliases` | Requested codes that stand for a code in the data | `{ "EA": "XM" }` |

Every field, with its type, limits and description, is listed in `config.schema.json`, and in the
[API reference](reference.md) under `DatasetConfig`.

### The data-quality rules

A rule runs only if it is listed. The order in the file does not matter. Each finding names the
rule, its severity and the series ([decision 0011](adr/0011-data-quality-rules.md)).

| Rule | Built | Parameters | What it reports |
|---|---|---|---|
| `unexpected_column` | yes | none | columns in the file that the configuration does not name; they are ignored |
| `unparseable_date` | yes | none | observations whose date is not in the configured format; dropped |
| `non_numeric_value` | yes | none | values that are not numbers within the configured precision and scale; kept as invalid, never rounded |
| `status_value_mismatch` | yes | none | an invalid status with a value, or a valid status with no value; kept as invalid |
| `exact_duplicate` | yes | none | rows repeated exactly; the copies are dropped |
| `conflicting_duplicate` | yes | `keep`: `first` or `last` | dates with more than one different observation; the configured one is kept |
| `unexpected_value` | yes | `expected`: a column name → its expected code | rows whose code differs from the expected one; a column the file does not have is skipped |
| `excess_decimals` | not yet | none | (planned) values with more decimals than the series' `DECIMALS` |
| `series_attribute_not_constant` | not yet | `resolve` | (planned) a series attribute that changes within a series |
| `plausible_range` | not yet | `min`, `max` | (planned) values outside a plausible range |

A rule that is listed but not built is logged and skipped by `transform`. Rule parameters are
checked by the rule when it runs, not when the configuration is loaded.

### A second dataset

A dataset of the same shape (a flat SDMX CSV from a bulk-download page) needs a new configuration,
not new code:

1. Copy `configs/bis_cbpol.json` to a new file and change `dataset` and `source`.
2. Declare the file's concepts in `structure`, and the series to keep in `structure.selection`.
3. Adjust `columns` to how the file is written, and the `unexpected_value` codes to the new file.
4. Load it to check it (the command at the end of this page), then run the commands with
   `--config configs/<new file>.json` and a separate `--data-dir`.

## `config.schema.json`: the format, described for tools

A [JSON Schema](https://json-schema.org/) is a JSON file that describes another JSON file: which
keys an object may have, which are required, the type of each value, its limits and a short
description. `config.schema.json` describes the configuration format.

### Where it comes from

??? note "For maintainers: how the schema is generated"

    It is generated from the configuration models, never written by hand. `render_json_schema()` in
    `policy_rate_monitor.config.schema` asks pydantic for the schema of `DatasetConfig`; pydantic
    writes one entry for each model, under `$defs`, and copies each field's docstring into the
    field's `description`. That is why the file is long (about 660 lines): it lists every field of
    every section with its type, limits and description.

    After a change to a model, regenerate it and commit it with the change:

    ```bash
    uv run bis-prates config schema > configs/config.schema.json
    ```

    A test compares the committed file with a fresh one. If they differ, it fails and prints that
    command.

### What it is for: help while you edit

The first key of the configuration points to the schema:

```json
{
  "$schema": "./config.schema.json",
  "dataset": { ... }
}
```

An editor with JSON Schema support reads that key and checks the file against the schema as you
type. VS Code does it with its built-in JSON support, with no extension. In an open configuration
file:

- **Key suggestions.** Inside an object, press `Ctrl+Space` (or type `"`) and a list shows the
  keys that object accepts, each with its description. Choosing one inserts it.
- **Mistakes underlined as you type.** An unknown key (`"titel"` instead of `"title"`), a wrong
  type (`"max_attempts": "three"`) or a number out of range (`"precision": 99`) gets a wavy
  underline, and the same message is listed in the Problems panel (`Ctrl+Shift+M`).
- **Descriptions on hover.** Hovering a key shows what it means, the text of the field's
  docstring.

The program itself never reads the schema; the `$schema` key is ignored when the configuration is
loaded.

### What it does not check

The schema checks each value on its own. The checks that compare values run only when the program
loads the configuration, for example:

- `source.fallback_url` must end with `source.file_name`;
- a column listed in `columns.unmapped` must not also be declared in `structure`;
- rule names and listed values must not repeat.

So a file the editor shows as clean can still fail to load. The full check is loading it:

```bash
bis-prates config validate --config configs/bis_cbpol.json
```

It prints `configs/bis_cbpol.json: valid (given with --config)`, or the problems found, with exit
code 4 ([CLI reference](cli.md#bis-prates-config-validate)).

Or, from Python, with the problems printed as the program prints them:

```python
from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.core.errors import ConfigError

try:
    config = FileConfigSource("configs/bis_cbpol.json").load()
except ConfigError as error:
    print(error)  # the file, then one line per problem
else:
    print(f"{config.dataset.id} is valid")
```
