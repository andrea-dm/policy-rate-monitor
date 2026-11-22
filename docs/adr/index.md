# Architecture decisions

Each record states one decision: the problem, what was decided and why, the alternatives that were
rejected, and what the decision costs. A record is not edited once accepted, apart from its links
and a dated update note that points to a later record; a later record supersedes it. The records
are part of the [Advanced](../advanced/index.md) pages, which describe the system as it is built.

Records are numbered in the order the decisions were taken, by the date of a record's first
decision. Decisions taken on the same day keep the order in which they were recorded. The records
were renumbered once, on 2026-11-20, to follow this order; the changelog lists the old file
names. A decision that is agreed but not built yet is written down
all the same, so that the numbering stays in that order; its status says so. A title in double
quotes is quoted as its record states it, from before the vocabulary of
[0004](0004-domain-model.md).

| Record | Decision | Decided | Status |
|---|---|---|---|
| [0001](0001-data-stack-pyarrow.md) | Arrow types and parquet for the data; Arrow tables at the provider interface | 2026-11-17 | Accepted |
| [0002](0002-charting-and-report-format.md) | matplotlib for the PNG, Plotly for interactive charts; the report format | 2026-11-17 | Accepted; its open point settled by 0015 |
| [0003](0003-web-extension-stack.md) | The web extension: a FastAPI service and a small React front end | 2026-11-17 | Accepted; the API is built, the front end is not |
| [0004](0004-domain-model.md) | Frozen pydantic models with checked invariants, and their vocabulary | 2026-11-17 | Accepted |
| [0005](0005-exact-decimal-rates.md) | Exact decimal rates end to end | 2026-11-17 | Accepted; refined by 0017 |
| [0006](0006-twelve-month-look-back.md) | "The 12-month look-back: binary search per area" | 2026-11-17 | Accepted; its search superseded by 0013 |
| [0007](0007-config-driven-design.md) | A config-driven design, with the configuration outside the package | 2026-11-17 | Accepted; refined by 0017 and 0018 |
| [0008](0008-provider-interface.md) | A source-agnostic `DataProvider` that returns canonical data | 2026-11-17 | Accepted |
| [0009](0009-sdmx-validation.md) | SDMX validation against BIS structural metadata, with no cache and no fallback | 2026-11-17 | Accepted, partly built; refined by 0018 |
| [0010](0010-cli-and-exit-codes.md) | A command line first, with one exit code per action | 2026-11-17 | Accepted; refined by 0017 |
| [0011](0011-data-quality-rules.md) | Data-quality rules named in the configuration, reported as findings | 2026-11-17 | Accepted, partly built; refined by 0017 |
| [0012](0012-elt-raw-and-staged-layers.md) | ELT, not ETL: load the source as it is, then stage it | 2026-11-19 | Accepted, partly built; refined by 0017 |
| [0013](0013-fast-analytics-numpy-numba.md) | Fast analytics: fixed-point arrays, NumPy kernels, Numba optional | 2026-11-19 | Accepted |
| [0014](0014-http-api.md) | The HTTP API: one service for both interfaces, jobs one at a time, errors as kinds | 2026-11-21 | Accepted; refined by 0017 |
| [0015](0015-report-templates-and-html.md) | The report pages from templates, with an offline chart and a PNG fallback | 2026-11-21 | Accepted |
| [0016](0016-accessible-charts.md) | Accessible charts: tableau-colorblind10, dash patterns and line labels, shared by both charts | 2026-11-21 | Accepted; refined by 0017 |
| [0017](0017-bad-input-failures-and-writes.md) | Bad input, failures and writes: strict on the way in, one exit code per action, files replaced whole | 2026-11-22 | Accepted |
| [0018](0018-sdmx-reference-and-not-evaluated.md) | The SDMX reference: a configuration section, read with pysdmx once per run, "not evaluated" as a finding | 2026-11-22 | Accepted |
