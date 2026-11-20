# Configuration

Everything the program knows about a dataset comes from one JSON file in `configs/`. This page
says what the two files there are, where the large one comes from, and what it is for.

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
configs/bis_cbpol.json: invalid configuration (1 error)
  source.max_attempts: Input should be a valid integer (got "three")
```

## `config.schema.json`: the format, described for tools

A [JSON Schema](https://json-schema.org/) is a JSON file that describes another JSON file: which
keys an object may have, which are required, the type of each value, its limits and a short
description. `config.schema.json` describes the configuration format.

### Where it comes from

It is generated from the configuration models, never written by hand. `render_json_schema()` in
`policy_rate_monitor.config.schema` asks pydantic for the schema of `DatasetConfig`; pydantic
writes one entry for each model, under `$defs`, and copies each field's docstring into the
field's `description`. That is why the file is long (about 660 lines): it lists every field of
every section with its type, limits and description.

After a change to a model, regenerate it from the repository root and commit it with the change:

```bash
uv run python -m policy_rate_monitor.config.schema configs/config.schema.json
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
uv run python -c "from policy_rate_monitor.config import FileConfigSource; FileConfigSource('configs/bis_cbpol.json').load()"
```
