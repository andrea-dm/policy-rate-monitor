# 0009 SDMX validation: check the data against BIS structural metadata, with no cache and no fallback

- **Status:** Accepted, not built yet (M7)
- **Decided:** 2026-11-17

## Context

Extension 2 of the brief asks to use the BIS SDMX API's structural metadata: the definition of the
dataflow (its dimensions and attributes) and its code lists. Two things can use it: validating the
data, and validating the area codes a user types, with suggestions (`GBR → GB`, `USA → US`).

Research on 2026-11-17 confirmed, with live calls:

- the dataflow `BIS:WS_CBPOL` has the structure `BIS:BIS_CBPOL(1.0)`;
- `REF_AREA` is coded by `CL_BIS_GL_REF_AREA` (239 codes, including `US`, `GB`, `JP`, `CH` and
  `XM`), not by `CL_AREA`;
- `pysdmx` retrieves both through its registry client;
- the same API can also serve the data, and its labelled SDMX-CSV 2.0 was byte-identical to the
  bulk file on the day it was compared.

## Decision

- **SDMX validation is data-quality work.** New rules, enabled in the configuration like every other
  rule ([0011](0011-data-quality-rules.md)), check that every area in the data is in the official code
  list, and that the data's dimensions and attributes match the dataflow definition. The rules read
  the reference through an interface, so tests replace it with a fake reference source.
- **The same code list checks the requested codes**, in the CLI and in the API, and suggests
  corrections. The report shows the official area names.
- **The reference comes only from the BIS structural metadata.** It is never derived from the data
  being validated: data that has not been validated cannot be its own reference.
- **No cache of the reference.** When the BIS SDMX API cannot be reached, the SDMX rules report
  "not evaluated" as an explicit finding, never a pass. Requested codes then get the core behaviour:
  aliases, the `UNKNOWN_REF_AREA` status, and a warning listing the codes the data has, with no
  suggestions.
- **A known limitation, stated.** SDMX validation depends on the BIS SDMX API being reachable at
  run time; a run without it is not SDMX-validated, and says so in the README and the report.
- **No SDMX data provider.** The brief scopes the extension to structural metadata. A data provider
  over the API stays a possible later adapter behind the provider interface
  ([0008](0008-provider-interface.md)).

## How it evolved

The SDMX work was first written down as "the SDMX adapter", then narrowed the same day to
validation, once the brief's scope was read closely. A fallback that built the reference from the
loaded data was proposed and rejected as circular ("what if the data already loaded have not been
validated?"). A cache of the last code list was proposed and rejected too: a stale reference is a
hidden risk, and stating the drawback is better than hiding it.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A reference built from the loaded data | Circular: unvalidated data cannot validate itself |
| Caching the last retrieved code list | A stale reference silently passes data it should flag |
| A separate `StructureProvider` beside `DataProvider` | The reference serves the data-quality rules; an interface for the rules is enough |
| An SDMX data provider now | Beyond the brief's scope; the bulk file already gives the same data |

## Consequences

- One more external service, with its own failure modes: timeouts and outages give "not
  evaluated" findings, not errors.
- `pysdmx` comes as an optional extra, like the other ends of the pipeline.
- The core already handles unknown codes safely (status `UNKNOWN_REF_AREA`, and a warning that lists
  the available codes), so the tool stays correct without this extension.

## Evidence

- `docs/milestones.md`, M7.
- Tests to write with it: a reachable, an unreachable and a mismatching reference, all through a
  fake reference source.
