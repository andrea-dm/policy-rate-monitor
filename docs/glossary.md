# Glossary

Each term has one meaning throughout the code, the outputs and these pages.

| Term | Meaning |
|---|---|
| **Country or economic area**, `ref_area` | A country or economic area, by its SDMX reference-area code (`US`, `XM`). Not "country": the euro area is not one. The command-line option is still called `--countries`, the word the brief uses. |
| **Requested code**, `requested_ref_area` | The code as the user typed it (`ea`), kept next to the code used (`XM`). |
| **Alias** | A requested code that stands for a code in the data, declared in `ref_area_aliases` (`EA → XM`). |
| **SDMX** | Statistical Data and Metadata eXchange: the standard the BIS uses to describe and publish its data. |
| **Concept** | One named column of the source in SDMX terms: a dimension or an attribute (`FREQ`, `OBS_STATUS`). |
| **Dimension** | A concept that identifies a series: frequency (`FREQ`) and reference area (`REF_AREA`). Together they form the series key. |
| **Attribute** | A concept that describes a series (`UNIT_MEASURE`, `TITLE`) or an observation (`OBS_STATUS`), without identifying it. |
| **Unit multiplier** | The power of ten a published value is to be multiplied by (`UNIT_MULT`); 0 for every policy-rate series. |
| **Series** | One time series of the source, identified by its **series key**: the dimension codes joined with `.` (`D.US`). Each country or economic area has one daily series. |
| **Series metadata** | The attributes published with a series: title, unit, decimals and the others (`SeriesMetadata`). Not to be confused with the configuration or the SDMX structural metadata. |
| **Observation** | One row of a series: a date, a value and a status. |
| **Valid observation** | An observation whose value is a number and whose status is not an invalid one (`M`, missing). Only valid observations are used for the latest and prior rates. |
| **Latest rate** | The last valid observation of a country's or area's series, at its own date. |
| **Target date** | The latest date minus 12 calendar months; a day that does not exist (29 February) becomes the last day of the month. |
| **Prior rate** | The last valid observation on or before the target date. |
| **Basis point** | One hundredth of a percentage point: a move from 4.00 % to 4.25 % is 25 basis points (bp). |
| **Look-back** | Going back from the latest date to the target date; 12 months by default (`lookback_months`). |
| **Stale** | Too old to pass for current: a latest rate far behind the newest date in the data, or a prior rate far before the target date (`stale_latest_days`, `stale_prior_days`). |
| **Change in basis points** | `(latest − prior) × 100`, computed exactly; 1 percentage point is 100 basis points. |
| **Snapshot** | The result of the calculation for one requested country or area: latest and prior rate, the change, a status and flags (`DataSnapshot`). |
| **Status** | The one outcome of a snapshot: `ok`, `unknown_ref_area`, `no_valid_data` or `insufficient_history`. |
| **Flag** | A warning on a snapshot that does not hide its values: `stale_latest`, `stale_prior`. |
| **Summary** | The output built from the snapshots: the table in the report, `summary.csv`, `summary.json`. |
| **Fingerprint** | The SHA-256 checksum of a file: two files with the same fingerprint have the same content. |
| **Release** | One version of the source file, identified by where it came from, when it was retrieved and its SHA-256 fingerprint (`SourceRelease`). "Snapshot" is never used for a file version. |
| **ELT** | Extract, load, transform: the source is first stored as it is, then transformed. The tool's raw and staged layers. |
| **Parquet** | A compact file format for tables that keeps each column's type; the format of the raw and staged layers. |
| **Arrow** | The in-memory table format behind parquet; the provider returns the observations as an Arrow table, with exact decimal rates. |
| **Raw layer** | The source as it was loaded, with nothing selected or cleaned (`data/raw/`). |
| **Staged layer** | The canonical data built from the raw layer: observations, series metadata, findings and the release they were built from (`data/staged/`). |
| **Canonical data** | The data in the one form every provider returns, whatever its source: the observations table in the schema built from the configuration, and the domain models. |
| **Provider** | An object that prepares the data (`fetch`, `transform`) and serves the canonical data (`DataProvider`). |
| **Configuration** | Our description of how to process a dataset: source, structure, rules, settings (`configs/bis_cbpol.json`). |
| **SDMX structural metadata** | The publisher's definition of the data: dimensions, attributes, code lists. Used only to validate the data (planned). |
| **Data-quality rule** | A named check, selected and set up in the configuration. |
| **Finding** | One result of a data-quality rule: the rule, its severity, the series, the count and what was done (`DataQualityIssue`). |
| **Cache lifetime** | How long a downloaded copy is reused without a new request (`cache_ttl_hours`, 24). |
| **Exit code** | The number a command returns when it ends: 0 for success, another number for each kind of failure ([CLI reference](cli.md#exit-codes)). |
