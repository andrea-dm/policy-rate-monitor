"""The PNG chart of the policy rates, drawn with matplotlib (base install).

It draws the same series and rows as the Plotly figure (``lines``), in the same style
(``style``).
"""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from matplotlib.figure import Figure

from policy_rate_monitor.chart.lines import charted_series, valid_rows
from policy_rate_monitor.chart.style import (
    LABEL_GAP,
    LINE_WIDTH,
    NO_DATA,
    label_positions,
    line_style,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from matplotlib.axes import Axes
    from pandas import DataFrame

    from policy_rate_monitor.core.models import DataSnapshot

__all__ = ["DPI", "rate_chart"]

DPI: Final = 120
"""The resolution the PNG is saved at, in dots per inch."""


def rate_chart(
    title: str,
    snapshots: Sequence[DataSnapshot],
    observations: DataFrame,
    start: date | None = None,
) -> Figure:
    """Draws the step chart of the policy rates of the requested countries or areas.

    The n-th line gets ``line_style(n)``, as in the Plotly figure, and is named at its
    right end. Save it at ``DPI`` so the lines have their width in pixels.

    Args:
        title: The chart title, the dataset's title.
        snapshots: One snapshot per requested country or area; those with a latest
            rate get a line.
        observations: The staged observations.
        start: First date on the chart; ``None`` for the whole history.

    Returns:
        The matplotlib figure.
    """
    # A bare Figure draws with the Agg canvas and leaves pyplot's global state alone.
    figure = Figure(figsize=(10, 5.5), layout="constrained")
    axes = figure.add_subplot()
    charted = charted_series(snapshots)
    ends: list[tuple[str, date, float, str]] = []
    for index, series in enumerate(charted):
        rows = valid_rows(observations, series.series_key, start)
        style = line_style(index)
        dates = rows.date.tolist()
        # Floats only for drawing; every number in the files stays an exact decimal.
        values = [float(value) for value in rows.value]
        axes.step(
            dates,
            values,
            where="post",
            label=series.ref_area_name,
            color=style.colour,
            linewidth=LINE_WIDTH * 72 / DPI,
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
