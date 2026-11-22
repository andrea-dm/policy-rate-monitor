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
| `reference` | Optional. Where the SDMX rules read the BIS structural metadata: the API's address, the dataflow, the country or area code list and the timeout (see "The SDMX reference" below) | `"timeout_seconds": 10` |
| `structure` | The source's concepts and their roles: the dimensions of the series key, the time period, the measure (with its precision, scale and unit multiplier), the observation and series attributes, which attribute fills each series-metadata field, and the series to keep | `"selection": { "FREQ": "D" }` |
| `columns` | How the concepts are written in the file: encoding, delimiter, the separators of `CODE:Label` headers and `code: label` values, the texts that mean "no value", the columns that are loaded but not used, and the ones that may be absent | `"missing_values": ["NaN"]` |
| `rules` | The data-quality rules to run, each with a severity and its parameters (next section) | `{ "name": "exact_duplicate", "severity": "info" }` |
| `snapshot` | The calculation: the look-back in months and the two staleness thresholds in days | `"lookback_months": 12` |
| `output` | The series-metadata fields added to each row of `summary.csv`, in column order | `["series_key", "ref_area_name", …]` |
| `ref_area_aliases` | Requested codes that stand for a code in the data | `{ "EA": "XM" }` |
| `ref_area_suggestions` | Optional. Requested codes that are not in the SDMX code list, with the code to suggest instead; offered, never applied (see "Suggestions for invalid codes" below) | `{ "GBR": "GB" }` |

Every field, with its type, limits and description, is listed in `config.schema.json`, and in the
[API reference](reference.md) under `DatasetConfig`.

### How the numbers are read

The measure in `structure` gives the column of the values, their precision and scale (the stored
type, `decimal128(15, 6)` for the BIS file) and the column of the unit multiplier; the
series-metadata fields name the column of the decimals:

- **A value** is read only as a plain ASCII number: an optional sign, digits, an optional decimal
  point and exponent, with ASCII spaces around it ignored. It must convert exactly and fit the precision
  and scale; it is never rounded. Anything else is kept as not valid, with no value, and reported
  as `non_numeric_value`.
- **The unit multiplier** (`UNIT_MULT`) must be a whole number: the value is multiplied by
  10^`UNIT_MULT`, exactly. A row whose multiplier is blank or not a whole number is kept as not
  valid, with a `non_numeric_value` finding; the other rows are staged as usual.
- **The decimals** (`DECIMALS`) only format the rates for display. A blank, non-whole or negative
  value leaves the series' decimals unknown, with a warning in the log; its rates are then shown
  with only the digits they need (no trailing zeros).

The reasons are in [decision 0017](adr/0017-bad-input-failures-and-writes.md).

### The data-quality rules

A rule runs only if it is listed. The order in the file does not matter. Each finding names the
rule, its severity and the series ([decision 0011](adr/0011-data-quality-rules.md)). What the
severities mean, what happens to the rows and what to do with a finding are in
[Data quality](data-quality.md).

| Rule | Built | Parameters | What it reports |
|---|---|---|---|
| `unexpected_column` | yes | none | columns in the file that the configuration does not name; they are ignored |
| `unparseable_date` | yes | none | observations whose date is not in the configured format; dropped |
| `non_numeric_value` | yes | none | values that are not plain numbers within the configured precision and scale, and rows whose unit multiplier is blank or not a whole number; kept as invalid, never rounded |
| `status_value_mismatch` | yes | none | an invalid status with a value, or a valid status with a missing value (a value that is not a number is `non_numeric_value`'s finding alone); kept as invalid |
| `exact_duplicate` | yes | none | rows repeated exactly; the copies are dropped |
| `conflicting_duplicate` | yes | `keep`: `first` or `last` | rows dropped because another row has the same series and date; the configured one is kept |
| `unexpected_value` | yes | `expected`: a column name → its expected code | rows whose code differs from the expected one; a column the file does not have is skipped |
| `ref_area_not_in_code_list` | yes | none (the ids are in `reference`) | rows whose country or area code is not in the official code list, one finding per series; kept |
| `dataflow_mismatch` | yes | none (the ids are in `reference`) | the differences between `structure` and the dataflow definition, in one finding for the dataset |
| `excess_decimals` | not yet | none | (planned) values with more decimals than the series' `DECIMALS` |
| `series_attribute_not_constant` | not yet | `resolve` | (planned) a series attribute that changes within a series |
| `plausible_range` | not yet | `min`, `max` | (planned) values outside a plausible range |

A rule that is listed but not built is logged and skipped by `transform`. The parameters of the
built rules are checked when the configuration is loaded, whether the rule is on or off, so
`bis-prates config validate` reports them: `keep` is `first` or `last`, and `expected` is an
object whose every column is a column of the configuration (in `structure` or in
`columns.unmapped`) and whose every code is a text. An SDMX rule, listed on or off, needs the
`reference` section.
Switching `conflicting_duplicate` off stops its findings; its `keep` still decides which row is
kept (`last` when absent).

### The SDMX reference

The `reference` section tells the two SDMX rules where to read what BIS publishes as the
definition of the data ([decision 0018](adr/0018-sdmx-reference-and-not-evaluated.md)):

```json
"reference": {
  "api_url": "https://stats.bis.org/api/v2",
  "dataflow": { "agency": "BIS", "id": "WS_CBPOL", "version": "1.0" },
  "ref_area_code_list": { "agency": "BIS", "id": "CL_BIS_GL_REF_AREA", "version": "1.0" },
  "timeout_seconds": 10
}
```

| Key | Meaning |
|---|---|
| `api_url` | The base address of the SDMX REST API |
| `dataflow` | The dataflow of the data, by agency, id and version. The structure checked against is the one the dataflow points to on the day |
| `ref_area_code_list` | The code list of the country or area codes. `dataflow_mismatch` also reports it when it is not the list the structure uses |
| `timeout_seconds` | The time allowed for each request, more than 0 |

No id may be empty. The section is optional: a configuration without it loads, as long as it lists
no SDMX rule. The rules need the `sdmx` extra (`pysdmx`); without it, or when the API cannot be
reached, each enabled one reports "not evaluated" ([Data quality](data-quality.md#the-checks-against-the-bis-structural-metadata)).

### Suggestions for invalid codes

When a requested code has no series, and the configuration has a `reference` section, the code is
checked against the SDMX code list
([How it works](how-it-works.md#8-countries-or-areas-the-tool-cannot-answer-for-13)). A code the
list does not have gets suggestions: its entry in `ref_area_suggestions`, then any code whose
official name is the requested text, case aside (`switzerland` → `CH`). Only codes of the list are
suggested. `bis_cbpol.json` maps the three-letter code of every country or area the data has
(`GBR` → `GB`, `USA` → `US`), and `UK`, `GER`, `SUI`, `EU`, `EZ` and `EMU`. `EU` → `XM` points to
the closest series: the EU is not the euro area.

When the configuration loads, a key must be upper case and must not also be an alias (an alias is
applied first, so the suggestion would never be offered), and no suggestion may lead to itself or
to another suggestion (exit 4).

### A second dataset

A dataset of the same shape (a flat SDMX CSV from a bulk-download page) needs a new configuration,
not new code:

1. Copy `configs/bis_cbpol.json` to a new file and change `dataset` and `source`, and `reference`
   (or drop it together with the two SDMX rules).
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
    field's `description`. That is why the file is long (about 740 lines): it lists every field of
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
- rule names and listed values must not repeat;
- `conflicting_duplicate`'s `keep` must be `first` or `last`, and `unexpected_value` must have
  an `expected` object, whose every column is a column of the configuration (in `structure` or
  in `columns.unmapped`) and every code a text;
- an SDMX rule needs the `reference` section.

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
