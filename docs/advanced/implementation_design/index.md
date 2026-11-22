# Implementation design

## Abstract

`bis-prates` downloads the central bank policy rates that the Bank for International Settlements
(BIS) publishes as one bulk file, stages them as an exact, validated dataset, and reports for each
requested country or economic area the latest rate, the rate 12 months earlier and the change in
basis points. It runs as a command line tool and, with the `web` extra, as an HTTP API. This report
describes the implementation as it is in the repository: the modules and their layering, the
contract of each module, the interfaces, the data model, the run-time behaviour, the failure modes,
the measured cost, the packaging, the logging and the security posture. Where something is planned
and not built, the report says so.

## Introduction

### Purpose

The report lets a reviewer or an auditor check, without reading all of the code first, what the
system promises and where each promise is kept. Each page states its claims next to the file, the
function, the configuration key or the test that backs them. A claim without such a reference is a
defect in the report.

### Scope

In scope: everything under `src/policy_rate_monitor/`, the configuration (`configs/bis_cbpol.json`
and `configs/config.schema.json`), the packaging (`pyproject.toml`), the checks that guard the design
(`tach.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`) and the measurements in
`diagnostics/README.md` and the decision records.

Out of scope: the web front end (decision [0003](../../adr/0003-web-extension-stack.md), milestone M8,
not built), the check of the requested codes against the BIS code list and the official names in the report
(decision [0009](../../adr/0009-sdmx-validation.md), milestone M7, not built yet), and the
data-quality rules that the configuration lists but the code does not implement
([Components](components.md#transform)). The pipelines around the code (CI/CD, release) are in
[Operations](../operations/index.md).

### Audience

Skilled users who embed the package, reviewers of a change, and auditors of the data it produces.
Readers who want the step-by-step story start at [How it works](../../how-it-works.md).

### Structure

| Page | Report section |
|---|---|
| [System overview](system_overview.md) | The architecture, the layering rule and the bounded parts |
| [Components](components.md) | The contract of each public module |
| [Interfaces](interfaces.md) | The CLI, the HTTP API, the provider interface, the configuration schema and the file formats |
| [Data model](data_model.md) | The raw and staged layers, the canonical schema, the domain models and their invariants |
| [Runtime behaviour](runtime_behavior.md) | The pipeline steps, the API's job runner and cache, threads, writes and retries |
| [Failure modes](failure_modes.md) | Every typed error, its exit code and HTTP status, and what stays on disk |
| [Performance](performance.md) | The measured figures, with their dates |
| [Deployment](deployment.md) | Packaging, extras, dependency groups, Python versions, how to run `serve` |
| [Observability](observability.md) | What is logged where, and what is not provided |
| [Security](security.md) | What the system trusts, what it checks and what it leaves open |

No section was merged or dropped: the system has enough for each. Two choices of scope are worth
stating. The report has no section on a deployment infrastructure, because the repository defines
none: the tool is a package and a process, with no container image, infrastructure code or
orchestration file. And "observability" is short on purpose: the system logs and records job times,
and has no metrics or tracing.

### Executive summary

The system is a three-step pipeline (`fetch`, `transform`, `report`) over files, with an HTTP API
that runs the same steps. The code is layered, and the layering is enforced by `tach` before every
commit and every push (the `tach-check` hook) and in the `tach` job of CI. Everything is driven by one validated configuration file, so a dataset of the same
shape needs no code change. Rates are exact decimals from the source file to the output files,
and the change in basis points is an exact subtraction, checked again when a result is built.
Bad input fails by a typed error, each with its own exit code and HTTP status, and a failed run
does not replace the previous files; one gap is known, that a reader in another process can pair
files of two runs while they are renamed into place (issue 35, tracked in the code). The
repository records the cost of the calculation (the snapshot of all 49 daily series takes about
0.2 s on the staged data) but no time or memory figure for `transform`, which reads the whole
477 MB CSV into memory ([Performance](performance.md)). The API has no authentication and sends no CORS headers: it is meant for
`127.0.0.1` or behind a proxy that checks who calls.
