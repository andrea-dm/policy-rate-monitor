"""Which series get a line on a chart, and the rows each line draws.

Both charts, the report's PNG and the Plotly figure, and the HTTP API's time series
use these, so they show the same series and the same rows. A chart names in its
legend and its text alternative only the lines it draws (``chart_lines``).

See also: [How it works](how-it-works.md), section 10, and [decision
0016](adr/0016-accessible-charts.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from policy_rate_monitor.core.models import SnapshotStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from pandas import DataFrame

    from policy_rate_monitor.core.models import DataSnapshot, SeriesMetadata

__all__ = ["chart_lines", "charted_series", "valid_rows"]

# A country or economic area with a latest rate gets a line on the chart.
_CHARTED = (SnapshotStatus.OK, SnapshotStatus.INSUFFICIENT_HISTORY)


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
    rows = observations[(observations.series_key == series_key) & observations.is_valid]
    if start is not None:
        rows = rows[rows.date >= start]
    return rows


def chart_lines(
    snapshots: Sequence[DataSnapshot],
    observations: DataFrame,
    start: date | None = None,
) -> list[tuple[SeriesMetadata, DataFrame]]:
    """Returns the lines a chart draws: the charted series with data from *start* on.

    A charted series with no valid row from *start* on (*start* is after its data) gets
    no line, so the legend and the text alternative do not name it.

    Args:
        snapshots: One snapshot per requested country or area.
        observations: The staged observations.
        start: First date on the chart; ``None`` for the whole history.

    Returns:
        The series and its rows, one pair per line, in request order.
    """
    lines: list[tuple[SeriesMetadata, DataFrame]] = []
    for series in charted_series(snapshots):
        rows = valid_rows(observations, series.series_key, start)
        if not rows.empty:
            lines += [(series, rows)]
    return lines
