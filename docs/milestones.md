# Milestones

This page describes how the BIS Policy Rate Monitor is built, step by step. Each milestone has a
goal, a scope and the condition that marks it as done. The page also explains why the work is cut
this way.

## Design principles behind every milestone

Two architectural principles apply to all milestones. They are set up in M0 and respected
everywhere after.

- **Config-driven.** Everything specific to a dataset, a source or an environment is declared in
  configuration, not written into the code. That covers the source location, column roles,
  encodings, validity rules, data-quality rules, thresholds, area aliases and output fields. The
  configuration is nested JSON. It is read through a `ConfigSource` interface: from a file shipped
  with the package today, and potentially from a REST service or a NoSQL store later. Pydantic
  models validate it when it is loaded. Rule *implementations* are Python code; the configuration
  only selects them and sets their parameters.
- **Source-agnostic.** The backend reads data only through a `DataProvider` interface, and every
  provider returns the same **canonical data**: one observations table with a schema derived from
  the configuration, plus series metadata, data-quality findings and provenance. Today's provider
  reads the BIS bulk download. Other providers can be added without changing analytics, reporting or
  the API: a query engine such as DuckDB or Spark, or another REST API such as the BIS SDMX data
  API.

The `DataProvider` interface has two kinds of methods:

| Kind | Methods | Purpose |
|---|---|---|
| Lifecycle | `fetch`, `transform` | Prepare the data. They change state (download, write the store) and map to the CLI commands. Each provider decides what they mean for its source. |
| Query | `list_series`, `series_metadata`, `observations`, `data_quality_issues`, `source_release` | Read the data, its findings and its provenance. Read-only, and the only way the rest of the backend sees data. |

## How the milestones were chosen

Four principles shaped the split:

1. **Correctness before breadth.** The core tool is fetch, transform, snapshot and report. It is
   finished, tested and documented before any optional extension starts. The core alone is a
   complete, submittable solution. Nothing in it depends on the extensions.
2. **One milestone per risk.** Each milestone isolates one kind of problem:
   - foundations: contracts that everything else depends on
   - fetch: network and source failures
   - transform: messy data
   - snapshot: calculation correctness
   - report: presentation and reproducibility

   That way a failure points to one place, and each milestone gets the tests its risk calls for.
3. **Follow the data flow.** The core milestones M1 to M4 follow the pipeline stages: a cached
   source file, then canonical data, then snapshots, then output files. Each handoff is a stable
   contract, so a stage can also be built against a fake of the stage before it (M3 uses data from
   the fake provider).
4. **Every milestone is verifiable on its own.** Each one ends with a working CLI command or
   function, tests that run without the network, and every repository check passing (ruff,
   pyright, deptry, tach, pytest).

```mermaid
flowchart LR
    M0[M0 Foundations] --> M1[M1 Acquisition]
    M1 --> M2[M2 Canonical data & quality]
    M0 --> M3[M3 Snapshot calculation]
    M2 --> M4[M4 Reporting]
    M3 --> M4
    M4 --> M5[M5 Core release v0.1.0]
    M5 --> M6[M6 HTTP API]
    M6 --> M7[M7 Web front end]
    M5 --> M8[M8 SDMX validation]
```

M3 depends only on M0. Its input is canonical data, so it can be built and tested with data from
the M0 fake provider and with generated series, in parallel with M1 and M2.

## Overview

Section numbers refer to the interview exercise brief. Ext. 1 (web front end) and Ext. 2 (BIS SDMX
API metadata) are the two optional extensions in its §5.

| Milestone | Delivers | Brief requirement | Track |
|---|---|---|---|
| M0 Foundations | Contracts: domain and configuration models, `ConfigSource`, `DataProvider` and a fake provider, canonical schema, CLI entry point | §2 Option A | Core |
| M1 Acquisition | `bis-prates fetch`: the bulk provider's discovery, cache and failure handling | §1.1 | Core |
| M2 Canonical data & quality | `bis-prates transform`: canonical daily observations, series metadata, configurable data-quality rules, query methods | §1.2, §3.3 (missing values, duplicates) | Core |
| M3 Snapshot calculation | Latest rate, rate 12 months earlier, change in basis points | §1.3, §3.3 (12-month change) | Core |
| M4 Reporting | `bis-prates report`: the four output files | §1.4, §3.2 | Core |
| M5 Core release | README (with the AI-usage note), documentation, verified outputs, tag `v0.1.0` | §3.1, §3.4 | Core |
| M6 HTTP API | FastAPI endpoints over the same service layer | §2 Option B, Ext. 1 | Extension |
| M7 Web front end | React interface driving the workflow | Ext. 1 | Extension |
| M8 SDMX validation | Data-quality rules and country-code checks against BIS structural metadata | Ext. 2 | Extension |

---

## M0 — Foundations

**Goal.** Fix the shared vocabulary, the configuration model and the interfaces before any feature
code depends on them.

**Scope.**

- Domain models in `core` (pydantic):
  - `SeriesMetadata`
  - `DataSnapshot`, with `SnapshotStatus` (`OK`, `UNKNOWN_AREA`, `NO_VALID_DATA`,
    `INSUFFICIENT_HISTORY`) and `SnapshotFlag` (`STALE_LATEST`, `STALE_PRIOR`)
  - `DataQualityIssue` and `DataQualitySeverity`
  - `SourceRelease`, which identifies a version of the source: its location, a version identifier
    and the retrieval time (for the bulk provider: URL, ETag, checksum, fetch time)
- Configuration models (dataset, source, structure, columns, rules, snapshot parameters, output
  fields), validated when they are loaded.
- The `ConfigSource` interface, plus a file implementation. The policy-rate dataset's JSON
  configuration lives in `configs/`, outside the package: the package holds only the models
  that validate it, and a JSON Schema generated from them.
- The canonical observations schema, derived from the structure section of the configuration.
- The `DataProvider` interface (lifecycle and query methods) and an in-memory **fake provider**
  for tests.
- The `observations` contract: at most one row per series and date, sorted by date within each
  series, with the validity flag set. Shared interface tests check it, and every provider must
  pass them.
- Typed errors in `core`, and the CLI's documented exit codes (`ExitCode`), which map them.
- The `bis-prates` entry point (the `cli` package) with the three subcommands stubbed. Options
  are resolved in the order flag, environment variable, configuration file: `--config` selects
  the configuration, and `--source` and `--data-dir` override where data comes from and goes.
- A `tach` module entry for each subpackage, enforcing the layering.

**Done when.**

- `bis-prates --help` lists `fetch`, `transform` and `report`.
- The configuration in `configs/` loads and validates. A malformed or missing configuration
  fails with a clear message and its own exit code.
- The fake provider passes the shared interface tests.
- All checks pass.

**Why it is its own milestone.** Every later milestone exchanges these types and depends on these
interfaces. Settling them first avoids rework, and lets the layering and the config-driven
approach be enforced from the first line of feature code instead of being retrofitted.

---

## M1 — Acquisition (`fetch`)

**Goal.** In the bulk provider, reliably obtain the latest "Central bank policy rates (CSV, flat)"
ZIP, and download it again only when it has changed.

**Scope.**

- Every source detail (discovery page, link pattern, fallback URL, cache lifetime) comes from the
  configuration.
- Find the download link on the BIS bulk-download page, with the configured fallback URL.
- Local cache plus a sidecar file holding the URL, `ETag`, `Last-Modified`, checksum and fetch
  time.
- A cache lifetime, then conditional requests: a `304 Not Modified` response means no download.
- Download to a temporary file, validate it (a real ZIP with exactly one CSV), then replace the
  cached copy atomically.
- Bounded retries on transient errors, and explicit timeouts.
- Clear errors for an unavailable source and for an unexpected format. If the network fails but a
  cached copy exists, use it and print a warning.

**Done when.**

- A second `fetch` makes no download.
- Each of these failure modes has a defined outcome, covered by tests against a mocked HTTP layer:
  server error, HTML returned instead of a ZIP, truncated archive, a ZIP without exactly one CSV,
  discovery link not found (fallback URL used), and network down with and without a cached copy.

**Why it is its own milestone.** This is the only component that talks to the outside world, so its
failure modes are different in kind from the rest. Building it first also gives transform,
reporting and the end-to-end tests a fast, offline, repeatable input.

---

## M2 — Canonical data & quality (`transform`)

**Goal.** Turn the raw file into validated canonical data, with the series metadata kept, and make
it available through the provider's query methods.

**Scope.**

- Read the columns and their roles from the configuration's structure section (dimensions, time,
  value, observation attributes, series attributes). No column name or code is written into the
  code.
- Apply the configured selection (daily series: `FREQ = D`), filtering on the code.
- Parse the BIS flat-CSV conventions: headers like `CODE:Label` and coded values like
  `code: label`.
- Produce canonical data:
  - **observations**: series key, dimensions, date, value as an exact decimal, status, validity
    flag
  - **series metadata**: title, unit, unit multiplier, decimals, source, notes on breaks, coverage
    dates
- Missing values are kept and marked invalid, using the configured encoding. The source encodes
  them as `NaN` with status `M`. A value is valid only when the status is not `M` **and** the value
  is a valid number.
- A **data-quality layer**. Rules are Python functions registered by name, and all report through
  the same `DataQualityIssue` model. The configuration chooses which ones apply, with what severity
  and which parameters. Checks on canonical data are source-independent and run in a shared layer;
  checks that need the raw file run inside each provider:
  - inside the bulk provider: required columns, unparseable dates or numbers, more decimals than
    the series declares
  - in the shared layer: duplicates (exact repeats dropped, conflicting repeats resolved by the
    configured rule and recorded), plausible ranges, metadata constant within a series, attribute
    values that differ from the configured expected values (source drift)
- Every rule result is written to a machine-readable data-quality report.
- The bulk provider stores the canonical data as parquet and serves it through the query methods
  `list_series`, `series_metadata`, `observations`, `data_quality_issues` and `source_release`.

**Done when.**

- Running on the real file produces the canonical data and the data-quality report.
- The bulk provider passes the same shared interface tests as the fake provider.
- Unit tests on small synthetic files cover missing values, duplicates, invalid values, excess
  decimals, implausible values, series metadata that changes within a series, and a changed
  schema.
- A test enables, disables and re-parameterises a rule through the configuration alone.

**Why it is its own milestone.** The brief ranks data correctness first, and nearly every quirk of
the source data surfaces here. The canonical data it produces is the stable contract that the
calculation, the reports and the API rely on.

---

## M3 — Snapshot calculation

**Goal.** For each requested area, compute the latest valid rate, the rate 12 months earlier and the
change in basis points.

**Scope.**

- The calculation is a pure function, `compute_snapshots(observations, requests)`, returning one
  `DataSnapshot` per requested area. The service layer reads the observations through the
  provider's query methods and passes them in.
- Take the parameters from the configuration: look-back length, staleness thresholds, area aliases.
- Normalise the requested codes: case, whitespace, aliases, duplicates.
- Latest **valid** observation per series. Each series uses its own latest date, since areas do not
  necessarily share one.
- Target date = the latest date minus 12 calendar months. The prior rate is the most recent valid
  observation on or before the target date. This covers weekends, holidays and series that only
  have business days.
- The lookup is a binary search on each series' sorted dates, relying on the `observations`
  contract from M0.
- The change in basis points is computed in decimal arithmetic, so a move from `3.75` to `4.00` is
  exactly `25` bp.
- Unknown areas, series with no valid data and series with too little history each return a status.
  They are not errors and do not stop the other areas.
- Flags for a stale latest observation and for a prior observation well before the target date.

**Done when.**

- Unit tests and property-based tests pass for these cases:
  - the target date falls on a weekend
  - 29 February
  - negative rates
  - history starting before 1970
  - different latest dates per area
  - insufficient history
  - a series with no valid data
  - unknown area
  - the euro-area alias
  - stale latest and stale prior flags
  - requested codes with mixed case, whitespace and duplicates
- On generated series, the calculation matches a deliberately simple reference implementation.

**Why it is its own milestone.** This is the headline calculation, and an error here would be the
most visible. It is a pure function over canonical observations, so it can be tested exhaustively
on generated series and fake-provider data, in parallel with M1 and M2.

---

## M4 — Reporting (`report`)

**Goal.** Produce the required outputs for a chosen set of countries and start date.

**Scope.**

- The metadata fields included in the outputs are chosen by the configuration.
- Data-quality findings and provenance are read through the provider's query methods.
- `out/summary.csv` and `out/summary.json`. Every requested area gets a row, including failed ones
  with their status. Rows carry selected series metadata. The JSON adds the full metadata and the
  provenance from the `SourceRelease` (source location, version identifier, retrieval time),
  plus the generation time.
- `out/policy_rates.png`: daily rate levels over the requested period, drawn with matplotlib as
  step lines, because policy rates change in discrete steps.
- `out/report.html`:
  - summary table
  - interactive Plotly chart (the same figure the web front end shows)
  - description of the source data
  - method and assumptions
  - data-quality findings
  - limitations

  It is written for a reader who has not seen the code.

**Done when.**

- `bis-prates report --countries "US,EA,GB,JP,CH" --start 2015-01-01` produces all four files.
- An end-to-end test, from a fixture ZIP to the output files, passes.

**Why it is its own milestone.** The report is what users actually read. Its concerns are wording,
formatting precision, chart readability and reproducible provenance, which are separate from
computing the numbers.

---

## M5 — Core release (`v0.1.0`)

**Goal.** Make the core solution complete, reproducible and explained. This is the submittable
checkpoint.

**Scope.**

- README:
  - installation
  - the three commands
  - reproducing `out/` from a clean clone
  - running the tests
  - assumptions and limitations
  - an AI-usage note (1–2 paragraphs, brief §3.4): which assistants were used, what was generated
    versus written, how the code was validated, and concrete issues found
- Documentation pages: architecture, configuration reference, data model and assumptions, CLI
  reference.
- Manual check of a few computed values against the BIS Data Portal, recorded in the docs.
- Committed outputs in `out/` and a `v0.1.0` tag.

**Done when.** A fresh clone, following only the README, reproduces `out/`, and every deliverable
in brief §3 is ticked.

**Why it is its own milestone.** Documentation and verification are deliverables in their own
right, not cleanup. Closing the core with a tagged release also marks a clear line before any
optional work begins.

---

## M6 — HTTP API (extension)

**Goal.** Expose the same functionality over HTTP for the web front end.

**Scope.**

- Endpoints:
  - available areas
  - summary for a list of countries
  - time series for one country
  - starting fetch and transform as background jobs, with a job-status endpoint
  - the policy-rate chart as Plotly figure JSON, built by the same code as the HTML report
- Endpoints call the same service layer as the CLI, which reads data through the provider
  interface. No logic is duplicated.

**Done when.** API tests pass against the fake provider, and on the same canonical data the
endpoints return the same numbers as `bis-prates report`.

**Why it is its own milestone.** It proves the core has a clean service boundary. It is also useful
without a UI, and it has to be in place before the front end can be built.

---

## M7 — Web front end (extension)

**Goal.** A small React interface for the workflow.

**Scope.**

- React with Vite, kept to a few small components.
- Country selection.
- Buttons to run retrieval, transformation and report generation, with processing status and
  readable error messages.
- A table of latest rates.
- An interactive history chart, rendered with `react-plotly.js` from figure JSON that the API sends.
- The front end computes nothing and talks only to the M6 API.

**Done when.** One documented command starts the API and the UI, and the full workflow can be run
from the browser.

**Why it is its own milestone.** It is a separate technology stack with its own build and tests.
Separating it from M6 keeps the concerns apart: the API is the contract, and the UI only consumes it.

---

## M8 — SDMX validation (extension)

**Goal.** Validate the data, and the requested country codes, against BIS structural metadata.

**Scope.**

- Retrieve the dataflow definition and the reference-area code list named in the configuration from
  the BIS SDMX REST API, using `pysdmx`. The data-quality rules read this reference through an
  interface, so tests can replace it with a fake reference source.
- New data-quality rules, enabled through the configuration like all others. For example: every
  reference area in the data exists in the official code list, and the data's dimensions and
  attributes match the dataflow definition.
- The same code list validates the requested country codes in the CLI (and in the API, when M6 is
  in place), and suggests corrections, for example `GBR → GB` or `USA → US`.
- Official area names in the report.
- The reference data comes **only** from BIS structural metadata. It is never derived from the data
  being validated, because data that has not yet been validated cannot be its own reference.
- The reference is **not cached**. When the BIS SDMX API cannot be reached, the affected rules
  report "not evaluated" as an explicit data-quality finding instead of passing. Requested codes get
  the core behaviour only: aliases are applied, an area with no series gets the `UNKNOWN_AREA`
  status, and no suggestions are made.
- **Known limitation**, documented in the README and the report: SDMX validation depends on the
  availability of the BIS SDMX API at run time. A run without it is not SDMX-validated, and says so.

**Done when.** The new rules run as part of `transform`, invalid codes produce suggestions in the
CLI (and in the API, when M6 is in place), tests cover a reachable, an unreachable and a mismatching
reference using a fake reference source, and the known limitation is documented.

**Why it is its own milestone.** It adds a second external source with its own failure modes. The
core already handles unknown codes safely through statuses, so this strengthens validation and
the user experience without being needed for correctness.

---

## Alternatives considered

- **Thin end-to-end slice first.** This means wiring all three commands with placeholder logic, then
  deepening each. It was not chosen. M0 provides the contracts and a fake provider, so each stage
  can be built and tested alone, and a placeholder pipeline would add little confidence.
- **Merging transform and snapshot.** Rejected. Data cleaning and the calculation fail in different
  ways and need different tests. Keeping them apart also keeps the calculation a pure function.
- **One milestone for the whole web extension.** Rejected. Splitting the API (M6) from the UI (M7)
  keeps each pull request reviewable. It also makes the backend/frontend boundary explicit, which
  is the part of the extension that matters most.
- **Building an SDMX data provider up front.** Rejected: with only one real source, the provider
  interface would be guessed before a second implementation exists. The interface is defined in M0
  and proven by the bulk provider and the fake provider. An SDMX data provider is one possible later
  provider, alongside a query engine or another REST API, and could be added without changing the
  rest of the backend.

## Tracking

Each milestone is a GitHub milestone. Its work is tracked in issues and done on branches from
`develop`. A milestone is closed when its pull requests merge with all checks passing. Status:

| Milestone | Status |
|---|---|
| M0 Foundations | Not started (project scaffold in place) |
| M1 Acquisition | Not started |
| M2 Canonical data & quality | Not started |
| M3 Snapshot calculation | Not started |
| M4 Reporting | Not started |
| M5 Core release | Not started |
| M6 HTTP API | Not started |
| M7 Web front end | Not started |
| M8 SDMX validation | Not started |
