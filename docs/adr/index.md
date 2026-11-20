# Architecture decisions

Each record states one decision: the problem, what was decided and why, the alternatives that were
rejected, and what the decision costs. A record is not edited once accepted; a later record
supersedes it.

Records are numbered in the order the decisions were taken. Records 0001 to 0004 (the
configuration-driven design, the provider interface, the domain model and the command line) were
decided earlier and are not written up yet.

| Record | Decision | Status |
|---|---|---|
| [0005](0005-data-stack-pyarrow.md) | Arrow types and parquet for the data; Arrow tables at the provider interface | Accepted |
| [0006](0006-exact-decimal-rates.md) | Exact decimal rates end to end | Accepted |
| [0007](0007-twelve-month-look-back.md) | The 12-month look-back: binary search per area | Accepted |
