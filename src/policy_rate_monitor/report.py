"""Write the report: a CSV and a JSON summary, a chart and a Markdown page.

``Report(config).export(args)`` takes the snapshots and the staged data and writes four
files to the output folder: ``summary.csv``, ``summary.json``, ``policy_rates.png`` and
``report.md``. Every text that names the dataset comes from the configuration.
"""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from matplotlib.figure import Figure

from policy_rate_monitor.core.models import SnapshotFlag, SnapshotStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from decimal import Decimal
    from pathlib import Path

    from pandas import DataFrame

    from policy_rate_monitor.config.models import DatasetConfig, MetadataField
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        DataSnapshot,
        SeriesMetadata,
        SourceRelease,
    )

__all__ = ["Report", "ReportArgs"]

CHART_FILE = "policy_rates.png"

# Areas with a latest rate get a line on the chart.
_CHARTED = (SnapshotStatus.OK, SnapshotStatus.INSUFFICIENT_HISTORY)

_LIMITATIONS = (
    (
        "- The report shows the rates as the source publishes them; it does not "
        "correct or fill in any value."
    ),
    (
        "- Areas are compared at their own latest date, so the dates in one table "
        "can differ. A stale flag points out an area that lags behind."
    ),
    (
        "- A gap in a series makes the prior rate older than the target date; such "
        "a gap is flagged when it is longer than the configured threshold."
    ),
    "- The data is as of the retrieval time above, not real time.",
    (
        "- The chart draws the rates as floating-point numbers; the tables and the "
        "summary files carry the exact values."
    ),
)

_STATUS_TEXT = {
    SnapshotStatus.OK: "the latest rate, the prior rate and the change are known.",
    SnapshotStatus.UNKNOWN_AREA: "the dataset has no series for the requested code.",
    SnapshotStatus.NO_VALID_DATA: "the series exists but has no valid observation.",
    SnapshotStatus.INSUFFICIENT_HISTORY: (
        "a latest rate exists, but the series has no valid observation on or "
        "before the target date, so there is no change."
    ),
}


@dataclass(frozen=True)
class ReportArgs:
    """What one report is built from.

    Attributes:
        snapshots: One snapshot per requested area, in request order.
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
    """The summary files, the chart and the Markdown report of one dataset."""

    def __init__(self, config: DatasetConfig) -> None:
        """Keeps the configuration every text and output field comes from.

        Args:
            config: The dataset configuration.
        """
        super().__init__()
        self._config = config

    def export(self, args: ReportArgs) -> list[Path]:
        """Writes the four files to ``args.out_dir``.

        Args:
            args: The snapshots, the staged data and where to write.

        Returns:
            The paths of ``summary.csv``, ``summary.json``, the chart and
            ``report.md``.
        """
        config = self._config
        out_dir = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now(UTC).replace(microsecond=0)

        csv_path = out_dir / "summary.csv"
        _write_csv(args.snapshots, config.output.metadata_fields, csv_path)

        json_path = out_dir / "summary.json"
        release = args.release
        summary = {
            "generated_at": generated_at.isoformat(),
            "source": None if release is None else release.model_dump(mode="json"),
            "dataset": {"id": config.dataset.id, "title": config.dataset.title},
            "snapshots": [item.model_dump(mode="json") for item in args.snapshots],
        }
        json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        chart_path = out_dir / CHART_FILE
        _write_chart(args, config.dataset.title, chart_path)

        md_path = out_dir / "report.md"
        md_path.write_text(self._markdown(args, generated_at), encoding="utf-8")

        return [csv_path, json_path, chart_path, md_path]

    # TODO(andrea-dm): render this page from a Jinja template.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/12
    def _markdown(self, args: ReportArgs, generated_at: datetime) -> str:
        config = self._config
        lines = [
            f"# {config.dataset.title}",
            "",
            f"Generated on {generated_at:%Y-%m-%d at %H:%M} UTC.",
            "",
            "## Summary",
            "",
            *_summary_table(args.snapshots),
            "",
            f"![{config.dataset.title}]({CHART_FILE})",
            "",
            "## About the data",
            "",
            config.dataset.description,
            "",
            f"{config.dataset.attribution}.",
            "",
            *_release_lines(args.release, len(args.series)),
            "",
            "## Method and assumptions",
            "",
            *self._method_lines(),
            "",
            "## Data quality",
            "",
            *_issue_table(args.issues),
            "",
            "## Limitations",
            "",
            *_LIMITATIONS,
            "",
        ]
        return "\n".join(lines)

    def _method_lines(self) -> list[str]:
        config = self._config
        structure = config.structure
        selection = ", ".join(
            f"{key} = {code}" for key, code in structure.selection.items()
        )
        months = config.snapshot.lookback_months
        missing = ", ".join(f"`{value}`" for value in config.columns.missing_values)
        lines = [
            f"- Only the series with {selection} are used.",
            (
                "- The latest rate of an area is its last valid observation; "
                "each area keeps its own latest date."
            ),
            (
                f"- The target date is the same calendar date {months} months "
                "before the latest date (29 February becomes 28 February). The "
                "prior rate is the most recent valid observation on or before the "
                "target date."
            ),
            (
                "- The change is the latest rate minus the prior rate, in basis "
                "points (1 percentage point = 100 bp), computed exactly in decimal "
                "arithmetic."
            ),
            (
                f"- Missing values (empty or {missing}) are kept and marked "
                "invalid, and so is a value that is not a number."
            ),
        ]
        for attribute in structure.observation_attributes:
            if attribute.invalid_codes:
                codes = ", ".join(attribute.invalid_codes)
                lines += [
                    f"- Observations with {attribute.id} {codes} are marked invalid."
                ]
        lines += [f"- {self._duplicates_text()}"]
        lines += [
            (
                f"- A latest rate more than {config.snapshot.stale_latest_days} days "
                "older than the newest date in the data is flagged "
                f"{SnapshotFlag.STALE_LATEST}; a prior rate more than "
                f"{config.snapshot.stale_prior_days} days before the target date is "
                f"flagged {SnapshotFlag.STALE_PRIOR}. A flag never hides a value."
            ),
            "",
            "Statuses:",
            "",
        ]
        lines += [f"- {status}: {text}" for status, text in _STATUS_TEXT.items()]
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


def _write_chart(args: ReportArgs, title: str, path: Path) -> None:
    observations = args.observations
    # A bare Figure draws with the Agg canvas and leaves pyplot's global state alone.
    figure = Figure(figsize=(10, 5.5), layout="constrained")
    axes = figure.add_subplot()
    plotted: set[str] = set()
    for snapshot in args.snapshots:
        if snapshot.status not in _CHARTED or snapshot.series is None:
            continue
        key = snapshot.series.series_key
        if key in plotted:
            continue
        plotted.add(key)
        rows = observations[
            (observations["series_key"] == key) & observations["is_valid"]
        ]
        if args.start is not None:
            rows = rows[rows["date"] >= args.start]
        # Floats only for drawing; every number in the files stays an exact decimal.
        values = [float(value) for value in rows["value"]]
        axes.step(
            rows["date"].tolist(),
            values,
            where="post",
            label=snapshot.series.ref_area_name,
        )
    axes.set_title(title)
    axes.set_ylabel("Per cent")
    axes.grid(visible=True, alpha=0.3)
    if plotted:
        axes.legend()
    else:
        axes.text(0.5, 0.5, "No data to plot", ha="center", transform=axes.transAxes)
    figure.savefig(path, dpi=120)


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|")


def _rate(value: Decimal | None, decimals: int | None) -> str:
    if value is None:
        return "-"
    if decimals is None:
        return _plain(value)
    return f"{value:.{decimals}f}"


def _summary_table(snapshots: Sequence[DataSnapshot]) -> list[str]:
    lines = [
        (
            "| Area | Code | Latest date | Latest (%) | Prior date | Prior (%) "
            "| Change (bp) | Status | Flags |"
        ),
        "|---|---|---|--:|---|--:|--:|---|---|",
    ]
    for snapshot in snapshots:
        name = "-" if snapshot.series is None else snapshot.series.ref_area_name
        decimals = None if snapshot.series is None else snapshot.series.decimals
        cells = [
            name,
            snapshot.ref_area,
            snapshot.latest_date or "-",
            _rate(snapshot.latest_value, decimals),
            snapshot.prior_date or "-",
            _rate(snapshot.prior_value, decimals),
            _plain(snapshot.change_bps) or "-",
            snapshot.status,
            ", ".join(snapshot.flags) or "-",
        ]
        lines += ["| " + " | ".join(_cell(cell) for cell in cells) + " |"]
    return lines


def _release_lines(release: SourceRelease | None, series_count: int) -> list[str]:
    if release is None:
        lines = ["No record of the source file's retrieval was found."]
    else:
        lines = [
            f"- Downloaded from: {release.location}",
            f"- Retrieved at: {release.retrieved_at:%Y-%m-%d %H:%M %Z}",
        ]
        if release.version_id is not None:
            lines += [f"- Version: {release.version_id}"]
        if release.checksum_sha256 is not None:
            lines += [f"- SHA-256 of the file: `{release.checksum_sha256}`"]
    return [*lines, "", f"The staged data holds {series_count} series."]


def _issue_table(issues: Sequence[DataQualityIssue]) -> list[str]:
    if not issues:
        return ["No findings."]
    lines = [
        "| Rule | Severity | Series | Count | Detail |",
        "|---|---|---|--:|---|",
    ]
    for issue in issues:
        cells = [
            issue.rule,
            issue.severity,
            issue.series_key or "all",
            issue.count,
            issue.detail,
        ]
        lines += ["| " + " | ".join(_cell(cell) for cell in cells) + " |"]
    return lines
