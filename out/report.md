# Central bank policy rates

Generated on 2026-11-21 at 21:02 UTC.

## Summary

| Country or area | Code | Latest date | Latest (%) | Prior date | Prior (%) | Change (bp) | Status | Flags |
|---|---|---|--:|---|--:|--:|---|---|
| United States | US | 2026-11-17 | 3.8750 | 2025-11-17 | 3.8750 | 0 | ✅ ok | - |
| Euro area | XM | 2026-11-17 | 2.5000 | 2025-11-17 | 2.0000 | 50 | ✅ ok | - |
| United Kingdom | GB | 2026-11-16 | 4.0000 | 2025-11-14 | 4.0000 | 0 | ✅ ok | - |
| Japan | JP | 2026-11-17 | 1.2500 | 2025-11-17 | 0.5000 | 75 | ✅ ok | - |
| Switzerland | CH | 2026-11-17 | 0.0000 | 2025-11-17 | 0.0000 | 0 | ✅ ok | - |

![Central bank policy rates](policy_rates.png)

## About the data

Policy rates set by central banks, as published by the Bank for International Settlements (BIS) in its WS_CBPOL dataflow. The pipeline uses the daily series only.

Source: Bank for International Settlements (BIS), central bank policy rates (WS_CBPOL).

- Downloaded from: https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip
- Retrieved at: 2026-11-19 22:44 UTC
- SHA-256 of the file: `79f614ab22c21d172694cd48e9a9d551c7196f53c525008bc7ea4da8831d6494`

The staged data holds 49 series.

## Method and assumptions

- Only the series with FREQ = D are used.
- The latest rate of a country or area is its last valid observation; each one keeps its own latest date.
- The target date is the same calendar date 12 months before the latest date (29 February becomes 28 February). The prior rate is the most recent valid observation on or before the target date.
- The change is the latest rate minus the prior rate, in basis points (1 percentage point = 100 bp), computed exactly in decimal arithmetic.
- Missing values (empty or `NaN`) are kept and marked invalid, and so is a value that is not a number.
- Observations with OBS_STATUS M are marked invalid.
- Exact duplicate rows are dropped. When a series has two different values on the same date, the last one in the file is kept.
- A latest rate more than 30 days older than the newest date in the data is flagged stale_latest; a prior rate more than 7 days before the target date is flagged stale_prior. A flag never hides a value.

### Status legend

- ✅ ok: the latest rate, the prior rate and the change are known.
- ❓ unknown_ref_area: the dataset has no series for the requested code.
- 🚫 no_valid_data: the series exists but has no valid observation.
- ⚠️ insufficient_history: a latest rate exists, but the series has no valid observation on or before the target date, so there is no change.

## Data quality

✅ No findings.

## Limitations

- The report shows the rates as the source publishes them; it does not correct or fill in any value.
- Each country or area is compared at its own latest date, so the dates in one table can differ. A stale flag points out one that lags behind.
- A gap in a series makes the prior rate older than the target date; such a gap is flagged when it is longer than the configured threshold.
- The data is as of the retrieval time above, not real time.
- The chart draws the rates as floating-point numbers; the tables and the summary files carry the exact values.
