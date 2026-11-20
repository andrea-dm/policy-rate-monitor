# Glossary

Each term has one meaning throughout the code, the outputs and these pages.

| Term | Meaning |
|---|---|
| **Area**, `ref_area` | A country or economic area, by its SDMX reference-area code (`US`, `XM`). Not "country": the euro area is not one. |
| **Requested code**, `requested_ref_area` | The code as the user typed it (`ea`), kept next to the code used (`XM`). |
| **Alias** | A requested code that stands for a code in the data, declared in `area_aliases` (`EA → XM`). |
| **Series** | One time series of the source, identified by its **series key**: the dimension codes joined with `.` (`D.US`). One area has one daily series. |
| **Series metadata** | The attributes published with a series: title, unit, decimals and the others (`SeriesMetadata`). Not to be confused with the configuration or the SDMX structural metadata. |
| **Observation** | One row of a series: a date, a value and a status. |
| **Valid observation** | An observation whose value is a number and whose status is not an invalid one (`M`, missing). Only valid observations are used for the latest and prior rates. |
| **Latest rate** | The last valid observation of an area's series, at its own date. |
| **Target date** | The latest date minus 12 calendar months; a day that does not exist (29 February) becomes the last day of the month. |
| **Prior rate** | The last valid observation on or before the target date. |
| **Change in basis points** | `(latest − prior) × 100`, computed exactly; 1 percentage point is 100 basis points. |
| **Snapshot** | The result of the calculation for one requested area: latest and prior rate, the change, a status and flags (`DataSnapshot`). |
| **Status** | The one outcome of a snapshot: `ok`, `unknown_area`, `no_valid_data` or `insufficient_history`. |
| **Flag** | A warning on a snapshot that does not hide its values: `stale_latest`, `stale_prior`. |
| **Summary** | The output built from the snapshots: the table in the report, `summary.csv`, `summary.json`. |
| **Release** | One version of the source file, identified by where it came from, when it was retrieved and its SHA-256 fingerprint (`SourceRelease`). "Snapshot" is never used for a file version. |
| **Raw layer** | The source as it was loaded, with nothing selected or cleaned (`data/raw/`). |
| **Staged layer** | The canonical data built from the raw layer: observations, series metadata and findings (`data/staged/`). |
| **Canonical data** | The data in the one form every provider returns, whatever its source: the observations table in the schema built from the configuration, and the domain models. |
| **Provider** | An object that prepares the data (`fetch`, `transform`) and serves the canonical data (`DataProvider`). |
| **Configuration** | Our description of how to process a dataset: source, structure, rules, settings (`configs/bis_cbpol.json`). |
| **SDMX structural metadata** | The publisher's definition of the data: dimensions, attributes, code lists. Used only to validate the data (planned). |
| **Data-quality rule** | A named check, selected and set up in the configuration. |
| **Finding** | One result of a data-quality rule: the rule, its severity, the series, the count and what was done (`DataQualityIssue`). |
| **Cache lifetime** | How long a downloaded copy is reused without a new request (`cache_ttl_hours`, 24). |
