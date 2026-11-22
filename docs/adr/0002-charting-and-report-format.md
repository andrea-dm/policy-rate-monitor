# 0002 Charting and the report format: matplotlib for the PNG, Plotly for interactive charts

- **Status:** Accepted, with one open point (how `report.html` embeds Plotly)
- **Decided:** 2026-11-17; the HTML report postponed on 2026-11-19

## Context

The brief (§1.4) asks for a summary table, a chart of the policy rates over time, and a short report
in Markdown or HTML. The chart has two audiences: a static file someone can open anywhere,
including in a code review, and, with the web front end (Ext. 1), an interactive chart in the
browser.

Plotly can draw both, but writing a static PNG from Plotly needs `kaleido`, and `kaleido` 1.x
drives a Chrome browser. Neither the development machine nor a clean environment has one.

## Decision

- **matplotlib draws `out/policy_rates.png`**: a step chart of every requested area that has data,
  from `--start` on. A step chart, because a policy rate holds until the next decision. Floats are
  used only to draw; every number in the files stays an exact decimal
  ([0005](0005-exact-decimal-rates.md)).
- **Plotly draws the interactive charts**: the chart in `out/report.html` and the chart in the web
  front end. The figure is built once, in Python, and the API sends it to the front end as figure
  JSON, so the two charts cannot drift apart. Plotly is an optional extra (`interactive`), so the
  base install stays small.
- **The report format.** The decision of 2026-11-17 was `out/report.html`, with the interactive
  chart. On 2026-11-19, to deliver a smaller, working tool by the deadline, v0.1.0 ships
  `out/report.md` with the PNG, and the HTML report moves to #12, rendered from templates together
  with the Markdown one.

## Open point

How `report.html` embeds the plotly.js library: inline (about 4–5 MB, above the 2,000 KB limit the
pre-commit hooks set for a committed file in `out/`), or from a CDN (small, but the chart needs
internet to show). The leaning is the CDN, with the PNG as the offline fallback. It is settled in
#12.

**Update, 2026-11-21:** settled by [0015](0015-report-templates-and-html.md): the JavaScript is
inline, so the page works offline, and the file is exempt from the size limit. The test cited in
Evidence is now `test_report_writes_the_five_files`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Plotly with `kaleido` for the PNG | `kaleido` 1.x needs Chrome, which the development machine and a clean environment lack |
| matplotlib only | No interactive chart for the report or the front end |
| Plotly only | No reliable static PNG without a browser |
| A chart library in JavaScript for the front end (Recharts) | A second definition of the same chart, which can drift from the report's |
| `report.md` only, for good | Easiest to review in Git, but no interactive chart; kept for v0.1.0, with the HTML report to follow |

## Consequences

- Two charting libraries, each used for what it does well.
- The base install draws the PNG with no browser and no optional extra.
- Until #12, the report has no interactive chart.

## Evidence

- `src/policy_rate_monitor/report.py` (`_write_chart`); `out/policy_rates.png` and `out/report.md`
  from a real run.
- `tests/test_report.py::test_report_writes_the_four_files`.

## Updates

- 2026-11-22: the code named in Evidence has moved: the PNG is drawn by `rate_chart` in
  `src/policy_rate_monitor/chart/static.py`, and written with the other files by `Report` in
  `src/policy_rate_monitor/report/export.py` ([0016](0016-accessible-charts.md)).
