"""Write the report: a CSV and a JSON summary, a chart, a Markdown and an HTML page.

``Report(config).export(args)`` takes the snapshots and the staged data and writes five
files to the output folder: ``summary.csv``, ``summary.json``, ``policy_rates.png``,
``report.md`` and ``report.html``. Both pages are rendered from the templates in
``templates/``, filled from one context. Every text that names the dataset comes from
the configuration.

See also: [How it works](how-it-works.md), section 10, and decisions
[0002](adr/0002-charting-and-report-format.md) and
[0015](adr/0015-report-templates-and-html.md).
"""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

import base64
import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, select_autoescape
from matplotlib.figure import Figure

from policy_rate_monitor.chart_style import (
    LABEL_GAP,
    LINE_WIDTH,
    NO_DATA,
    alt_text,
    label_positions,
    line_style,
)
from policy_rate_monitor.core.models import (
    DataQualitySeverity,
    SnapshotFlag,
    SnapshotStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    from matplotlib.axes import Axes
    from pandas import DataFrame

    from policy_rate_monitor.config.models import DatasetConfig, MetadataField
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        DataSnapshot,
        SeriesMetadata,
        SourceRelease,
    )

__all__ = [
    "ICONS",
    "Report",
    "ReportArgs",
    "charted_series",
    "summary_document",
    "valid_rows",
]

# The control characters HTML forbids (all but whitespace). Plotly's code has some in
# its string literals, where a JavaScript escape stands for the same character.
_CONTROL = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")

CHART_FILE = "policy_rates.png"
"""File name of the chart in the output folder."""

_DPI = 120

# A country or economic area with a latest rate gets a line on the chart.
_CHARTED = (SnapshotStatus.OK, SnapshotStatus.INSUFFICIENT_HISTORY)

_LIMITATIONS = (
    (
        "The report shows the rates as the source publishes them; it does not "
        "correct or fill in any value."
    ),
    (
        "Each country or area is compared at its own latest date, so the dates "
        "in one table can differ. A stale flag points out one that lags behind."
    ),
    (
        "A gap in a series makes the prior rate older than the target date; such "
        "a gap is flagged when it is longer than the configured threshold."
    ),
    "The data is as of the retrieval time above, not real time.",
    (
        "The chart draws the rates as floating-point numbers; the tables and the "
        "summary files carry the exact values."
    ),
)

_STATUS_TEXT = {
    SnapshotStatus.OK: "the latest rate, the prior rate and the change are known.",
    SnapshotStatus.UNKNOWN_REF_AREA: (
        "the dataset has no series for the requested code."
    ),
    SnapshotStatus.NO_VALID_DATA: "the series exists but has no valid observation.",
    SnapshotStatus.INSUFFICIENT_HISTORY: (
        "a latest rate exists, but the series has no valid observation on or "
        "before the target date, so there is no change."
    ),
}

ICONS: dict[str, str] = {
    SnapshotStatus.OK: "✅",
    SnapshotStatus.UNKNOWN_REF_AREA: "❓",
    SnapshotStatus.NO_VALID_DATA: "🚫",
    SnapshotStatus.INSUFFICIENT_HISTORY: "⚠️",
    SnapshotFlag.STALE_LATEST: "⏳",
    SnapshotFlag.STALE_PRIOR: "⏳",
    DataQualitySeverity.INFO: "ℹ️",  # the emoji, not the letter i # noqa: RUF001
    DataQualitySeverity.WARNING: "⚠️",
    DataQualitySeverity.ERROR: "❌",
}
"""The emoji beside each status, flag and severity name, never instead of it."""


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
        # Autoescaping for the HTML page only: Markdown has no escaping of its own.
        self._templates = Environment(
            loader=PackageLoader("policy_rate_monitor", "templates"),
            autoescape=select_autoescape(["html.j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._templates.filters["md_cell"] = _md_cell

    def export(self, args: ReportArgs) -> list[Path]:
        """Writes the five files to ``args.out_dir``.

        Args:
            args: The snapshots, the staged data and where to write.

        Returns:
            The paths of ``summary.csv``, ``summary.json``, the chart,
            ``report.md`` and ``report.html``.
        """
        config = self._config
        out_dir = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now(UTC).replace(microsecond=0)

        csv_path = out_dir / "summary.csv"
        _write_csv(args.snapshots, config.output.metadata_fields, csv_path)

        json_path = out_dir / "summary.json"
        summary = summary_document(config, args.snapshots, args.release, generated_at)
        json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        title = config.dataset.title
        names = [series.ref_area_name for series in charted_series(args.snapshots)]
        chart_alt = alt_text(title, names, args.start)
        chart_path = out_dir / CHART_FILE
        _chart_figure(args, title).savefig(
            chart_path, dpi=_DPI, metadata={"Title": title, "Description": chart_alt}
        )

        context = self._context(args, generated_at)
        context["chart_alt"] = chart_alt
        context["chart_html"] = _interactive_chart(args, title)
        context["chart_png"] = base64.b64encode(chart_path.read_bytes()).decode("ascii")

        md_path = out_dir / "report.md"
        md_path.write_text(self._render("report.md.j2", context), encoding="utf-8")

        html_path = out_dir / "report.html"
        html_path.write_text(self._render("report.html.j2", context), encoding="utf-8")

        return [csv_path, json_path, chart_path, md_path, html_path]

    def _render(self, name: str, context: dict[str, object]) -> str:
        return self._templates.get_template(name).render(context)

    def _context(self, args: ReportArgs, generated_at: datetime) -> dict[str, object]:
        """The values both pages show; the templates hold the layout."""
        dataset = self._config.dataset
        return {
            "title": dataset.title,
            "generated_iso": generated_at.isoformat(),
            "generated_date": f"{generated_at:%Y-%m-%d}",
            "generated_time": f"{generated_at:%H:%M}",
            "summary_rows": _summary_rows(args.snapshots),
            "chart_file": CHART_FILE,
            "description": dataset.description,
            "attribution": dataset.attribution,
            "release": _release(args.release),
            "series_count": len(args.series),
            "method": self._method_lines(),
            "statuses": [(str(status), text) for status, text in _STATUS_TEXT.items()],
            "issue_rows": _issue_rows(args.issues),
            "limitations": _LIMITATIONS,
            "icons": ICONS,
        }

    def _method_lines(self) -> list[str]:
        config = self._config
        structure = config.structure
        selection = ", ".join(
            f"{key} = {code}" for key, code in structure.selection.items()
        )
        months = config.snapshot.lookback_months
        missing = ", ".join(f"`{value}`" for value in config.columns.missing_values)
        lines = [
            f"Only the series with {selection} are used.",
            (
                "The latest rate of a country or area is its last valid "
                "observation; each one keeps its own latest date."
            ),
            (
                f"The target date is the same calendar date {months} months "
                "before the latest date (29 February becomes 28 February). The "
                "prior rate is the most recent valid observation on or before the "
                "target date."
            ),
            (
                "The change is the latest rate minus the prior rate, in basis "
                "points (1 percentage point = 100 bp), computed exactly in decimal "
                "arithmetic."
            ),
            (
                f"Missing values (empty or {missing}) are kept and marked "
                "invalid, and so is a value that is not a number."
            ),
        ]
        for attribute in structure.observation_attributes:
            if attribute.invalid_codes:
                codes = ", ".join(attribute.invalid_codes)
                lines += [
                    f"Observations with {attribute.id} {codes} are marked invalid."
                ]
        lines += [self._duplicates_text()]
        lines += [
            (
                f"A latest rate more than {config.snapshot.stale_latest_days} days "
                "older than the newest date in the data is flagged "
                f"{SnapshotFlag.STALE_LATEST}; a prior rate more than "
                f"{config.snapshot.stale_prior_days} days before the target date is "
                f"flagged {SnapshotFlag.STALE_PRIOR}. A flag never hides a value."
            )
        ]
        return lines

    def _duplicates_text(self) -> str:
        keep = None
        for rule in self._config.rules:
            if rule.name == "conflicting_duplicate" and rule.enabled:
                keep = rule.params.get("keep")
        text = "Exact duplicate rows are dropped."
        if keep is not None:
            text += (
                " When a series has two different values on the same date, the "
                f"{keep} one in the file is kept."
            )
        return text


def _plain(value: Decimal | None) -> str:
    """Writes a decimal exactly, without trailing zeros: 4.000000 -> 4."""
    if value is None:
        return ""
    return f"{value.normalize():f}"


def _write_csv(
    snapshots: Sequence[DataSnapshot],
    metadata_fields: Sequence[MetadataField],
    path: Path,
) -> None:
    header = [
        "requested_ref_area",
        "ref_area",
        *metadata_fields,
        "latest_date",
        "latest_value",
        "prior_date",
        "prior_value",
        "change_bps",
        "status",
        "flags",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        for snapshot in snapshots:
            metadata = [
                "" if snapshot.series is None else getattr(snapshot.series, field)
                for field in metadata_fields
            ]
            writer.writerow(
                [
                    snapshot.requested_ref_area,
                    snapshot.ref_area,
                    *["" if value is None else str(value) for value in metadata],
                    snapshot.latest_date or "",
                    _plain(snapshot.latest_value),
                    snapshot.prior_date or "",
                    _plain(snapshot.prior_value),
                    _plain(snapshot.change_bps),
                    snapshot.status,
                    ";".join(snapshot.flags),
                ]
            )


def summary_document(
    config: DatasetConfig,
    snapshots: Sequence[DataSnapshot],
    release: SourceRelease | None,
    generated_at: datetime,
) -> dict[str, object]:
    """Returns the content of ``summary.json``, which the HTTP API also returns.

    Args:
        config: The dataset configuration.
        snapshots: One snapshot per requested country or area.
        release: Where and when the source file was retrieved; ``None`` if unknown.
        generated_at: When the summary was made.

    Returns:
        A JSON-ready dict: ``generated_at``, ``source``, ``dataset`` and
        ``snapshots``.
    """
    return {
        "generated_at": generated_at.isoformat(),
        "source": None if release is None else release.model_dump(mode="json"),
        "dataset": {"id": config.dataset.id, "title": config.dataset.title},
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
    }


def charted_series(snapshots: Sequence[DataSnapshot]) -> list[SeriesMetadata]:
    """Returns the series that get a line on a chart, once each, in request order.

    A country or economic area gets a line when it has a latest rate (status ok or
    insufficient history).

    Args:
        snapshots: One snapshot per requested country or area.

    Returns:
        The metadata of each charted series.
    """
    charted: dict[str, SeriesMetadata] = {}
    for snapshot in snapshots:
        series = snapshot.series
        if snapshot.status in _CHARTED and series is not None:
            charted.setdefault(series.series_key, series)
    return list(charted.values())


def valid_rows(
    observations: DataFrame, series_key: str, start: date | None = None
) -> DataFrame:
    """Returns the valid observations of one series, from *start* on.

    Args:
        observations: The staged observations.
        series_key: The series to keep.
        start: The first date to keep; ``None`` keeps the whole history.

    Returns:
        The rows, in the order of *observations* (by date for staged data).
    """
    rows = observations[
        (observations["series_key"] == series_key) & observations["is_valid"]
    ]
    if start is not None:
        rows = rows[rows["date"] >= start]
    return rows


def _chart_figure(args: ReportArgs, title: str) -> Figure:
    # A bare Figure draws with the Agg canvas and leaves pyplot's global state alone.
    figure = Figure(figsize=(10, 5.5), layout="constrained")
    axes = figure.add_subplot()
    charted = charted_series(args.snapshots)
    ends: list[tuple[str, date, float, str]] = []
    for index, series in enumerate(charted):
        rows = valid_rows(args.observations, series.series_key, args.start)
        style = line_style(index)
        dates = rows["date"].tolist()
        # Floats only for drawing; every number in the files stays an exact decimal.
        values = [float(value) for value in rows["value"]]
        axes.step(
            dates,
            values,
            where="post",
            label=series.ref_area_name,
            color=style.colour,
            linewidth=LINE_WIDTH * 72 / _DPI,
            linestyle=_matplotlib_dash(style.dash),
        )
        if values:
            ends += [(series.ref_area_name, dates[-1], values[-1], style.label_colour)]
    axes.set_title(title)
    axes.set_ylabel("Per cent")
    axes.grid(visible=True, alpha=0.3)
    if charted:
        axes.legend()
    if ends:
        _label_line_ends(axes, ends)
    else:
        axes.text(0.5, 0.5, NO_DATA, ha="center", transform=axes.transAxes)
    return figure


def _matplotlib_dash(pattern: tuple[int, ...]) -> str | tuple[int, tuple[float, ...]]:
    # matplotlib scales a dash pattern by the line width, so pixels divide by it.
    if not pattern:
        return "solid"
    return (0, tuple(length / LINE_WIDTH for length in pattern))


def _label_line_ends(axes: Axes, ends: list[tuple[str, date, float, str]]) -> None:
    low, high = axes.get_ylim()
    positions = label_positions(
        [value for _, _, value, _ in ends], (high - low) * LABEL_GAP
    )
    for (name, day, _, colour), position in zip(ends, positions, strict=True):
        axes.annotate(
            name,
            xy=(day, position),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            color=colour,
            annotation_clip=False,
        )


def _interactive_chart(args: ReportArgs, title: str) -> str | None:
    """The Plotly chart with its JavaScript inline, or ``None`` without the extra."""
    try:
        # Plotly is in the interactive extra, so a base install must not need it.
        from policy_rate_monitor.charts import rate_figure  # noqa: PLC0415
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


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _rate(value: Decimal | None, decimals: int | None) -> str:
    if value is None:
        return "-"
    if decimals is None:
        return _plain(value)
    return f"{value:.{decimals}f}"


def _summary_rows(snapshots: Sequence[DataSnapshot]) -> list[list[str]]:
    rows: list[list[str]] = []
    for snapshot in snapshots:
        name = "-" if snapshot.series is None else snapshot.series.ref_area_name
        decimals = None if snapshot.series is None else snapshot.series.decimals
        rows += [
            [
                name,
                snapshot.ref_area,
                str(snapshot.latest_date or "-"),
                _rate(snapshot.latest_value, decimals),
                str(snapshot.prior_date or "-"),
                _rate(snapshot.prior_value, decimals),
                _plain(snapshot.change_bps) or "-",
                str(snapshot.status),
                ", ".join(snapshot.flags) or "-",
            ]
        ]
    return rows


def _release(release: SourceRelease | None) -> dict[str, str | None] | None:
    if release is None:
        return None
    return {
        "location": release.location,
        "retrieved_at": f"{release.retrieved_at:%Y-%m-%d %H:%M %Z}",
        "version_id": release.version_id,
        "checksum": release.checksum_sha256,
    }


def _issue_rows(issues: Sequence[DataQualityIssue]) -> list[list[str]]:
    return [
        [
            issue.rule,
            str(issue.severity),
            issue.series_key or "all",
            str(issue.count),
            issue.detail,
        ]
        for issue in issues
    ]
