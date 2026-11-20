# Architecture decisions

Each record states one decision: the problem, what was decided and why, the alternatives that were
rejected, and what the decision costs. A record is not edited once accepted, apart from its links;
a later record supersedes it.

Records are numbered in the order the decisions were taken. Decisions taken on the same day keep
the order in which they were recorded. A decision that is agreed but not built yet is written down
all the same, so that the numbering stays in that order; its status says so.

| Record | Decision | Decided | Status |
|---|---|---|---|
| [0001](0001-data-stack-pyarrow.md) | Arrow types and parquet for the data; Arrow tables at the provider interface | 2026-11-17 | Accepted |
| [0002](0002-charting-and-report-format.md) | matplotlib for the PNG, Plotly for interactive charts; the report format | 2026-11-17 | Accepted, one open point |
| [0003](0003-web-extension-stack.md) | The web extension: a FastAPI service and a small React front end | 2026-11-17 | Accepted, not built yet |
| [0004](0004-domain-model.md) | Frozen pydantic models with checked invariants, and their vocabulary | 2026-11-17 | Accepted |
| [0005](0005-exact-decimal-rates.md) | Exact decimal rates end to end | 2026-11-17 | Accepted |
| [0006](0006-twelve-month-look-back.md) | The 12-month look-back: binary search per area | 2026-11-17 | Accepted; its search superseded by 0013 |
| [0007](0007-config-driven-design.md) | A config-driven design, with the configuration outside the package | 2026-11-17 | Accepted |
| [0008](0008-provider-interface.md) | A source-agnostic `DataProvider` that returns canonical data | 2026-11-17 | Accepted |
| [0009](0009-sdmx-validation.md) | SDMX validation against BIS structural metadata, with no cache and no fallback | 2026-11-17 | Accepted, not built yet |
| [0010](0010-cli-and-exit-codes.md) | A command line first, with one exit code per action | 2026-11-17 | Accepted |
| [0011](0011-data-quality-rules.md) | Data-quality rules named in the configuration, reported as findings | 2026-11-17 | Accepted, partly built |
| [0012](0012-elt-raw-and-staged-layers.md) | ELT, not ETL: load the source as it is, then stage it | 2026-11-19 | Accepted, partly built |
| [0013](0013-fast-analytics-numpy-numba.md) | Fast analytics: fixed-point arrays, NumPy kernels, Numba optional | 2026-11-20 | Accepted |
