"""``Report(config).export(args)``: the five output files of one report.

It makes ``summary.csv`` and ``summary.json`` (``summary``), the PNG chart
(``chart.static``) and the two pages (``pages``), with the interactive chart in
``report.html`` when Plotly is installed. All five are made in memory first, then
written together with ``core.files.write_files``, so a failure never leaves new files
beside old ones.

See also: [How it works](how-it-works.md), section 10, and decisions
[0012](adr/0012-elt-raw-and-staged-layers.md) and
[0015](adr/0015-report-templates-and-html.md).
"""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from policy_rate_monitor.chart.lines import chart_lines
from policy_rate_monitor.chart.static import DPI, rate_chart
from policy_rate_monitor.chart.style import alt_text
from policy_rate_monitor.core.files import write_files
from policy_rate_monitor.report.pages import CHART_FILE, Pages
from policy_rate_monitor.report.summary import summary_csv, summary_document

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path

    from pandas import DataFrame

    from policy_rate_monitor.config.models import DatasetConfig
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        DataSnapshot,
        SeriesMetadata,
        SourceRelease,
    )

__all__ = ["Report", "ReportArgs"]

# The control characters HTML forbids (all but whitespace). Plotly's code has some in
# its string literals, where a JavaScript escape stands for the same character.
_CONTROL = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class ReportArgs:
    """What one report is built from.

    Attributes:
        snapshots: One snapshot per requested country or area, in request order.
        observations: The staged observations (``series_key``, ``date``, ``value``,
            ``is_valid`` and the configured columns).
        series: Metadata of every staged series.
        issues: The data-quality findings of the transform.
        release: Where and when the source file was retrieved; ``None`` if unknown.
        out_dir: The output folder, created if needed.
        start: First date on the chart; ``None`` for the whole history.
    """

    snapshots: Sequence[DataSnapshot]
    observations: DataFrame
    series: Sequence[SeriesMetadata]
    issues: Sequence[DataQualityIssue]
    release: SourceRelease | None
    out_dir: Path
    start: date | None = None


class Report:
    """The summary files, the chart and the two report pages of one dataset."""

    def __init__(self, config: DatasetConfig) -> None:
        """Keeps the configuration and loads the page templates.

        Args:
            config: The dataset configuration every text and output field comes from.
        """
        super().__init__()
        self._config = config
        self._pages = Pages(config)

    def export(self, args: ReportArgs) -> list[Path]:
        """Writes the five files to ``args.out_dir``, all of them or none.

        Args:
            args: The snapshots, the staged data and where to write.

        Returns:
            The paths of ``summary.csv``, ``summary.json``, the chart,
            ``report.md`` and ``report.html``.

        Raises:
            OSError: A file cannot be written; the files already there are left as
                they were. Or a file cannot be renamed into place; the files of the
                set are removed.
        """
        config = self._config
        out_dir = args.out_dir
        generated_at = datetime.now(UTC).replace(microsecond=0)

        csv_text = summary_csv(args.snapshots, config.output.metadata_fields)
        summary = summary_document(config, args.snapshots, args.release, generated_at)
        json_text = json.dumps(summary, indent=2) + "\n"

        title = config.dataset.title
        lines = chart_lines(args.snapshots, args.observations, args.start)
        names = [series.ref_area_name for series, _ in lines]
        chart_alt = alt_text(title, names, args.start)
        png = _png_chart(args, title, chart_alt)

        context = self._pages.context(args, generated_at)
        context["chart_alt"] = chart_alt
        context["chart_html"] = _interactive_chart(args, title)
        context["chart_png"] = base64.b64encode(png).decode("ascii")

        markdown = self._pages.render("report.md.j2", context)
        html = self._pages.render("report.html.j2", context)
        files = {
            out_dir / "summary.csv": csv_text.encode("utf-8"),
            out_dir / "summary.json": json_text.encode("utf-8"),
            out_dir / CHART_FILE: png,
            out_dir / "report.md": markdown.encode("utf-8"),
            out_dir / "report.html": html.encode("utf-8"),
        }
        write_files(files)
        return list(files)


def _png_chart(args: ReportArgs, title: str, chart_alt: str) -> bytes:
    figure = rate_chart(title, args.snapshots, args.observations, args.start)
    png = io.BytesIO()
    figure.savefig(
        png, format="png", dpi=DPI, metadata={"Title": title, "Description": chart_alt}
    )
    return png.getvalue()


def _interactive_chart(args: ReportArgs, title: str) -> str | None:
    """The Plotly chart with its JavaScript inline, or ``None`` without the extra."""
    try:
        # Plotly is in the interactive extra, so a base install must not need it.
        from policy_rate_monitor.chart.interactive import rate_figure  # noqa: PLC0415
    except ImportError as error:
        if (error.name or "").partition(".")[0] != "plotly":
            raise
        return None
    figure = rate_figure(title, args.snapshots, args.observations, args.start)
    # Inline, not from a CDN, so the chart shows without a network. The height is fixed
    # because the page gives the chart's box none.
    chart = figure.to_html(
        full_html=False, include_plotlyjs=True, default_height="34rem"
    )
    return _CONTROL.sub(lambda match: f"\\u{ord(match[0]):04x}", chart)
