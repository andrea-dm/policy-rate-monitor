"""Write the report: a CSV and a JSON summary, a chart, a Markdown and an HTML page.

``Report(config).export(args)`` takes the snapshots and the staged data and writes five
files to the output folder: ``summary.csv``, ``summary.json``, ``policy_rates.png``,
``report.md`` and ``report.html``. Both pages are rendered from the templates in
``templates/``, filled from one context. Every text that names the dataset comes from
the configuration.

- ``export``: ``Report`` and ``ReportArgs``;
- ``summary``: ``summary.csv`` and ``summary.json``;
- ``pages``: the two pages and the values they show.

See also: [How it works](how-it-works.md), section 10, and decisions
[0002](adr/0002-charting-and-report-format.md) and
[0015](adr/0015-report-templates-and-html.md).
"""

from __future__ import annotations

from policy_rate_monitor.report.export import Report, ReportArgs
from policy_rate_monitor.report.pages import CHART_FILE, ICONS
from policy_rate_monitor.report.summary import summary_document

__all__ = ["CHART_FILE", "ICONS", "Report", "ReportArgs", "summary_document"]
