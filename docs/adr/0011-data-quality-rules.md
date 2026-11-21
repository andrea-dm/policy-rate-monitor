# 0011 Data-quality rules: named in the configuration, reported as findings, never silently fixed

- **Status:** Accepted; partly built (seven of the ten configured rules)
- **Decided:** 2026-11-17; scope cut on 2026-11-19

## Context

The BIS file has the usual problems of a real source, and profiling it showed which ones matter:
missing values published as rows (`NaN` with status `M`), dates and values that may not parse,
possible duplicates, implausible values in a few series (Brazil reached 790,799 %, Argentina 557 %),
and attributes that might change without notice. The brief (§3.3) asks how missing values,
duplicates and inconsistent data are handled. A reviewer, and later an operator, must be able to
see what was found and what was done about it.

## Decision

- **Rules are named in the configuration** ([0007](0007-config-driven-design.md)), each with a
  severity (`info`, `warning`, `error`) and its parameters, and can be switched off. The code
  implements them; the configuration selects and parameterises them.
- **Every result is a `DataQualityIssue`**: the rule, its severity, the series, the number of rows
  and a message saying what was done. `transform` writes them to `staged/data_quality.json`; the
  provider serves them; the report lists them.
- **Nothing is silently fixed or deleted.** A missing or unparseable value is kept as an invalid
  observation with no value; an exact copy of a row is dropped and counted; of two different rows for
  the same series and date, the configured one is kept (`keep: last` or `first`) and the conflict is
  reported; implausible values are to be flagged, never dropped. The raw layer keeps every row as
  loaded ([0012](0012-elt-raw-and-staged-layers.md)).
- **A rule decides what is reported, not whether the data is cleaned.** The staged data obeys the
  provider contract (one row per series and date) whatever the configuration says; switching a rule
  off only stops its finding.
- **What is built.** Seven rules run in `transform`: `unexpected_column`, `unparseable_date`,
  `non_numeric_value`, `status_value_mismatch`, `exact_duplicate`, `conflicting_duplicate` and
  `unexpected_value` (source drift: an attribute differing from the value the configuration
  expects). Three are configured but not built yet: `excess_decimals` (more decimals than the
  series' `DECIMALS`), `series_attribute_not_constant` and `plausible_range`. A configured rule that
  is not built is logged as "not implemented yet" at every run, never ignored silently.

## How it evolved

The design of 2026-11-17 had a registry of rule functions, with the checks that need the raw source
inside the provider and the checks on canonical data in a shared layer that every provider would
use. On 2026-11-19, to deliver a working tool by the deadline, the registry was cut: the rules the
brief asks about were built inside `transform`, and the remaining ones were left configured and
documented as not implemented. The shared layer is worth building when a second provider exists to
share it.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Dropping bad rows silently | Hides what the source did and what the tool did about it |
| Rejecting the whole file on any problem | One bad row would stop every report |
| One validator per source | The checks on canonical data would be written again for every provider |
| Thresholds and codes in the code | Every change to the source becomes a code change |

## Consequences

- The data-quality report shows what was found and what was done, run by run.
- Until the shared layer exists, a second provider would have to call the same checks itself.
- Three configured rules do nothing yet; the log and the README say so.

## Evidence

- `src/policy_rate_monitor/transform.py` (`IMPLEMENTED_RULES`, `_add_issues`); the `rules` section
  of `configs/bis_cbpol.json`.
- `tests/test_transform.py`: `test_missing_values_are_kept_and_marked_invalid`,
  `test_duplicates_are_dropped_and_reported`, `test_unparseable_dates_are_dropped_and_reported`,
  `test_a_value_with_more_decimals_than_the_type_is_never_rounded`.
- `diagnostics/check_record_figures.py`: the highest rate of each area (Brazil, Argentina).
