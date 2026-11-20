# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-11-20

The first release: the `bis-prates` command line, from the BIS bulk download to the report.

### Added

- `bis-prates fetch`: finds the "Central bank policy rates (CSV, flat)" ZIP on the BIS
  bulk-download page (with a configured fallback URL), downloads it with timeouts and a bounded
  number of attempts, checks that it is a ZIP holding exactly one CSV, and records its URL,
  retrieval time and SHA-256. A copy younger than 24 hours is reused; a failed download never
  replaces it; with the network down, the cached copy is used with a warning (#10).
- `bis-prates transform`: stores the CSV as loaded, then stages the daily series as exact
  decimals with the unit multiplier applied and a validity flag per observation, the series
  metadata, and the data-quality findings (unparseable dates or numbers, exact and conflicting
  duplicates, unexpected attribute values) (#10).
- `bis-prates report`: per requested area, the latest valid rate, the most recent valid rate on
  or before the same calendar date 12 months earlier, and the change in basis points, computed
  exactly; statuses for an unknown area, no valid data and insufficient history, and flags for a
  stale latest or prior rate. Writes `summary.csv`, `summary.json`, `policy_rates.png` and
  `report.md` (#11). A requested code the data does not have gets a warning listing the codes it
  has, with their names.
- One exit code per kind of failure (#10).
- `DataProvider`, the interface through which the CLI reads data, with the canonical observations
  schema built from the configuration, `BulkFileProvider` over the bulk-download file,
  `InMemoryProvider` for tests, and contract tests every provider passes (#6).
- The dataset configuration `configs/bis_cbpol.json`, its JSON Schema, and the configuration
  models that validate it when it is loaded (#5).
- The domain models and typed errors (#2).
- The outputs of a real run in `out/`, and the README with installation, usage, the data model,
  assumptions, known issues of the BIS data, limitations and the AI usage note.
- `diagnostics/`: the spikes and benchmarks behind the technical choices, including a comparison
  of pure Python, Numba and Cython on one analytical kernel.
- CI: ruff, pyright (strict), deptry, tach, the docs build, dependency audits and the package
  build on Python 3.12, and pytest with the docstring examples on Python 3.12, 3.13 and 3.14.

[Unreleased]: https://github.com/andrea-dm/policy-rate-monitor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/andrea-dm/policy-rate-monitor/releases/tag/v0.1.0
