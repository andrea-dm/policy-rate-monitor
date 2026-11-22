# 0007 A config-driven design: every dataset fact in a JSON configuration outside the package

- **Status:** Accepted
- **Decided:** 2026-11-17

## Context

The tool reads one dataset today, the BIS central bank policy rates, but nothing about the problem
is specific to it: another SDMX-style dataset has dimensions, a time column, a measure, attributes
and its own quirks. If the code names columns, codes and thresholds, every new dataset, and every
change the BIS makes to its file, is a code change.

The word "metadata" already has two other meanings here: the publisher's SDMX structural
metadata, and the series attributes that come with the data. The project's own description of a
dataset needs a name that cannot be confused with them.

## Decision

- **Config-driven.** Everything specific to a dataset, a source or an environment is declared in a
  configuration: the source (discovery page, file name, fallback URL, cache lifetime, attempts and
  timeouts), the structure (dimensions, time, measure with its precision and scale, observation and
  series attributes, the selection), the CSV conventions, the data-quality rules with their
  severity and parameters, the snapshot settings, the output fields and the area aliases. The
  structure section follows SDMX.
- **Rules in Python, chosen by the configuration.** Rule implementations are Python code, selected
  and parameterised by name in the configuration. There is no home-made rule language.
- **JSON, nested.** It is what a REST API or a document store (MongoDB, Cosmos DB) serves
  natively, so the same configuration can come from a file today and from a service later.
- **A `ConfigSource` interface.** `FileConfigSource` reads a file; a source for a service would
  implement the same `load()`. Every source passes the document to `parse_config`, so every source
  validates the same way and gives the same messages.
- **Validated when it is loaded.** Pydantic models (`config/models.py`) check the configuration
  and its cross-references (a selection or a series-metadata field naming an undeclared concept,
  an alias to a code that is itself aliased, a two-digit year format). Rule parameters are checked
  by the rule itself, when it runs. A mistake stops the program with one line per problem, at its
  exact location.
- **Outside the package.** The configuration lives in `configs/` at the repository root, not in
  the package. The package holds only the contract: the models, from which
  `configs/config.schema.json` is generated for editors and other tools.
- **Three kinds of metadata, kept apart:** the *configuration* (ours: how to process a dataset);
  the *SDMX structural metadata* (the publisher's definition of the data, used only to validate it,
  [0009](0009-sdmx-validation.md)); the *series metadata* (attributes that come with the data, in
  `SeriesMetadata`).

## How it evolved

The principle was first called "metadata-driven" and renamed "config-driven" the same day, so that
"metadata" keeps its two other meanings; `MetadataSource` became `ConfigSource` for the same reason.
The format was first YAML, and changed to JSON the same day, so that the configuration can later
come from a REST API or a document store. A suggestion that the BIS SDMX API could serve as a
configuration source was withdrawn: SDMX structure is the publisher's definition of the data, not
our processing configuration.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Column names and codes in the code | Every new dataset or source change becomes a code change |
| A rule language in the configuration | A second language to design, document and test; Python rules selected by name are enough |
| YAML | Easier to edit by hand, but not what the services we may read the configuration from serve |
| TOML | Same reason; awkward for deep nesting |
| The configuration inside the package | Ties one dataset's settings to a code release (Twelve-Factor III: keep configuration apart from code) |
| `pydantic-settings` | The configuration is a JSON document, not a set of environment variables |

## Consequences

- A new dataset of the same shape needs a new configuration, not new code; a test checks that the
  `config` package itself names no BIS-specific value.
- The schema must be regenerated and committed when a model changes; a test fails until it is.
- The configuration is long, because it states what other tools leave implicit. The page
  [Configuration](../configuration.md) explains it.

## Evidence

- `configs/bis_cbpol.json`, `configs/config.schema.json`, `src/policy_rate_monitor/config/`.
- `tests/config/`: `test_bis_cbpol_loads`, `test_config_package_holds_no_dataset_facts`,
  `test_every_failing_field_is_listed`, `test_reference_to_an_undeclared_concept_is_rejected`,
  `test_committed_schema_is_current`, `test_source_without_load_cannot_be_instantiated`.

## Updates

- 2026-11-22 (#34): the parameters of the built rules (`conflicting_duplicate`'s `keep`,
  `unexpected_value`'s `expected`) are checked when the configuration is loaded, whether the rule
  is on or off ([0017](0017-bad-input-failures-and-writes.md)).
- 2026-11-22 (#37): a new optional section, `reference`, names the BIS SDMX API, the dataflow and
  the country or area code list that the SDMX rules check the data against; listing an SDMX rule
  without it stops the configuration from loading. The ids and the address are in the
  configuration only, and the test of the `config` package also checks the `reference` and
  `sdmx` modules for them ([0018](0018-sdmx-reference-and-not-evaluated.md)).
