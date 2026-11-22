"""The two report pages, ``report.md`` and ``report.html``, from Jinja templates.

Both are filled from one context, so they cannot disagree: the code prepares the values
and the templates in ``templates/`` hold the layout. Autoescaping is on for HTML and off
for Markdown.

See also: [decision 0015](adr/0015-report-templates-and-html.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, select_autoescape

from policy_rate_monitor.core.models import (
    DataQualitySeverity,
    SnapshotFlag,
    SnapshotStatus,
)
from policy_rate_monitor.report.summary import plain

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from policy_rate_monitor.config.models import DatasetConfig
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        DataSnapshot,
        SourceRelease,
    )
    from policy_rate_monitor.report.export import ReportArgs

__all__ = ["ICONS", "Pages"]

CHART_FILE = "policy_rates.png"
"""File name of the chart in the output folder."""

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


class Pages:
    """The templates of the two pages and the values they show."""

    def __init__(self, config: DatasetConfig) -> None:
        """Keeps the configuration and loads the templates.

        Args:
            config: The dataset configuration every text comes from.
        """
        super().__init__()
        self._config = config
        # Autoescaping for the HTML page only: Markdown has no escaping of its own.
        self._templates = Environment(
            loader=PackageLoader("policy_rate_monitor.report", "templates"),
            autoescape=select_autoescape(["html.j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._templates.filters["md_cell"] = _md_cell

    def render(self, name: str, context: dict[str, object]) -> str:
        """Renders one page.

        Args:
            name: The template, ``report.md.j2`` or ``report.html.j2``.
            context: The values, from ``context`` and the chart.

        Returns:
            The page.
        """
        return self._templates.get_template(name).render(context)

    def context(self, args: ReportArgs, generated_at: datetime) -> dict[str, object]:
        """Returns the values both pages show, apart from the chart.

        Args:
            args: The snapshots, the staged data and where to write.
            generated_at: When the report was made.

        Returns:
            The template context; the caller adds ``chart_alt``, ``chart_html``
            and ``chart_png``.
        """
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
        return (
            "Exact duplicate rows are dropped. When a series has two different values "
            f"on the same date, the {self._config.conflict_keep()} one in the file is "
            "kept."
        )


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _rate(value: Decimal | None, decimals: int | None) -> str:
    if value is None:
        return "-"
    if decimals is None:
        return plain(value)
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
                plain(snapshot.change_bps) or "-",
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
