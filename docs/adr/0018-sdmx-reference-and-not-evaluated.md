# 0018 The SDMX reference: a configuration section, read with pysdmx once per run, "not evaluated" as a finding

- **Status:** Accepted
- **Decided:** 2026-11-22 (#37)

## Context

[Decision 0009](0009-sdmx-validation.md) fixes what the SDMX validation does: data-quality rules
that check the data against the BIS structural metadata, read through an interface, with no cache,
and with a "not evaluated" finding when the reference cannot be obtained. It leaves open how the
reference is named, where the interface lives, how it is fetched, what a "not evaluated" finding
looks like, and what exactly the comparison with the dataflow definition covers.

What BIS serves was checked live on 2026-11-22, with `pysdmx` 1.22.0:

- the dataflow `BIS:WS_CBPOL(1.0)` points to the structure `BIS:BIS_CBPOL(1.0)`, whose
  `REF_AREA` is coded by `BIS:CL_BIS_GL_REF_AREA(1.0)` (239 codes, all 49 of today's data among
  them);
- the v2 API answered each query in well under a second;
- the dataflow query that `pysdmx`'s `get_schema` sends returns cut-down code lists, and the
  `/schema/` endpoint answers with XML whatever format is asked for;
- BIS lists the time dimension under `timeDimensions`, which `pysdmx` 1.22.0 does not read: the
  structure comes back without `TIME_PERIOD`, and with no error.

## Decision

- **A `reference` section in the configuration**, beside `source`: the API's address, the
  dataflow, the reference-area code list (each as agency, id and version) and one timeout. No id
  or address is written in the code ([0007](0007-config-driven-design.md)). Listing an SDMX rule,
  on or off, without the section stops the configuration from loading (exit 4), as the parameters
  of the other rules do ([0017](0017-bad-input-failures-and-writes.md)).
- **The configuration names the dataflow, not the structure.** The structure is the one the
  dataflow points to on the day, so a dataflow moved to a new structure is checked against the new
  one. The code list is named too, as 0009 says, and `dataflow_mismatch` reports it when it is not
  the list the structure uses for the reference area.
- **The interface lives in its own module, `reference`**, under `transform` and `bulk`: an
  abstract `ReferenceSource` with two questions (`structure()`, `ref_area_codes()`), the models
  they answer with, and `UnavailableReference`, whose answers always raise
  `ReferenceUnavailableError`. The default, `NO_REFERENCE`, is one of them. The `pysdmx` source is
  a separate module, `sdmx`, in the new `sdmx` extra; `cli` imports it only for `transform` and
  `serve`, and only when an SDMX rule is enabled.
- **`pysdmx` fetches.** Its registry client asks for the dataflow, then the structure, then the
  code list: the queries that give full code lists. `get_schema` and `get_dataflow_details` are not
  used. Every `pysdmx` error, and an answer that cannot be decoded, becomes
  `ReferenceUnavailableError` with a plain reason; any other exception is a bug.
- **One attempt per question, once per run.** Nothing is kept between runs, so a long-running
  `serve` never checks against an old reference; that would be a cache, which 0009 rules out.
  The reference is asked only after the file is read and its selection has rows, so a missing or
  bad file ends as before, with no SDMX request.
- **"Not evaluated" is a finding with `count` 0**: one per enabled SDMX rule, for the whole
  dataset, with the rule's severity and the detail `not evaluated: <reason>`, and a warning in the
  log for each reason. This holds for every reason: the API cannot be reached, it has no such dataflow or code
  list (the reason names the configured id), it answers with an error, or the `sdmx` extra is not
  installed. `transform` ends with exit 0. Getting the reference is optional; checking the data is
  not.
- **What `dataflow_mismatch` compares**: the configuration's `structure` with the definition, by
  component id: a component declared and not defined, a component defined and neither declared nor
  in `columns.unmapped`, a different role (dimension, measure, attribute), a different `coded`, and
  the code list of the reference area. The time dimension is left out, since `pysdmx` cannot see it
  in BIS's answer; attachment levels and whether a mandatory attribute has values are not compared.
- **What `ref_area_not_in_code_list` counts**: the rows of the configured selection whose
  reference-area code, without its label, is not in the code list, one finding per series, as
  `unexpected_value` does. No row is dropped or changed.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A new field on the finding (`evaluated`, or an outcome) | Changes a core model, the report's columns, both templates and the data-model docs; the model already set `count` 0 aside for this |
| A `not_evaluated` severity | Severity says how serious a finding is, not whether the rule ran, and it is set per rule |
| The ids and the address in each rule's `params` | Repeated in each rule, and the later check of requested codes could not reach them cleanly |
| Taking the code list from the structure | One value fewer, but 0009 and the milestone name it in the configuration; `dataflow_mismatch` checks that the two agree |
| Naming the structure instead of the dataflow | Saves one request, but a dataflow moved to a new structure would be checked against the old one |
| Fetching with the project's own HTTP code, `pysdmx` only parsing | Would reuse the bulk file's timeouts and stand-ins, but 0009 has `pysdmx` retrieve the reference, and parsing alone was not checked |
| Retries, as for the bulk file | A failure costs only a finding; retries would add waits to every offline `transform` |
| Exit 7 on a 404 or another error answer | Getting the reference is optional, and 0009 makes its failures findings; the reason names the id to fix |
| The "not implemented yet" log line when the extra is missing | Easy to miss; 0009 asks for an explicit finding |
| Stopping `transform` (exit 2) when an SDMX rule is enabled and the extra is missing, as `serve` does without the `web` extra | Considered on 2026-11-22 and not chosen: the base install must keep working with `bis_cbpol.json`, the data is still held to the configuration and checked by the other rules, and the "not evaluated" finding in the report says the run was not checked against BIS |
| Making `pysdmx` a base dependency, so the checks always run when BIS is reachable | Considered on 2026-11-22 and not chosen: 0009 makes it an optional extra, like the other ends of the pipeline, and it adds `httpx`, `msgspec` and `parsy` to every install |
| Reading `TIME_PERIOD` from the raw answer beside `pysdmx` | The answer would be parsed twice; a missing time column already stops the run |
| Comparing the file's columns with the definition | The file is already held to the configuration, and role and coding can only be compared on the configuration |
| Checking every coded value against its code list, and mandatory attributes for values | 0009 names the reference-area code only; `TIME_FORMAT` is empty on every row, so the second check would flag the whole dataset |
| Building the source for every command | Would load `pysdmx` for `--version` and `report`, which never use it |

## Consequences

- `transform` with an SDMX rule enabled needs the BIS SDMX API: up to three requests per run (one
  for the code list, two for the dataflow definition), each with
  the configured timeout. Offline, or without the extra, the run still succeeds, with one "not
  evaluated" finding per rule; the README states the limitation.
- A finding with `count` 0 now exists, and the report shows it like any other ("all" in the series
  column).
- When `pysdmx` reads BIS's time dimension, a test on a recorded BIS answer fails, as a reminder
  to look again at what `dataflow_mismatch` compares.
- The default tests run without the network, with a fake source and recorded answers; a live test
  (`pytest -m integration`) checks the committed configuration against BIS.

## Evidence

- `src/policy_rate_monitor/reference.py`, `src/policy_rate_monitor/sdmx.py`,
  `transform._check_reference`, `cli.main._reference`; the `reference` section and the two rules
  in `configs/bis_cbpol.json`.
- `tests/test_transform_sdmx.py`, `tests/test_sdmx.py` (on the answers recorded in
  `tests/data/sdmx/`), `tests/config/test_models.py`, `tests/test_cli.py`
  (`test_transform_without_the_sdmx_extra_reports_not_evaluated_and_exits_0`,
  `test_the_base_commands_load_neither_the_web_extra_nor_the_report_steps`), and
  `tests/integration/test_sdmx_api.py`.
- A run on 2026-11-22 against the live API, on the release of 19 November 2026: three requests,
  no SDMX finding, exit 0.
