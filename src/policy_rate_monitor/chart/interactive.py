"""The interactive chart of the policy rates, as a Plotly figure.

It draws the same series and the same rows as the report's ``policy_rates.png``
(``lines``), in the same style (``style``). The HTTP API returns it as JSON for the web
front end, and ``report.html`` embeds it; it needs the ``interactive`` extra (Plotly).

See also: [HTTP API](api.md) and decisions
[0002](adr/0002-charting-and-report-format.md) and
[0016](adr/0016-accessible-charts.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plotly.graph_objects import Figure, Scatter

from policy_rate_monitor.chart.lines import charted_series, valid_rows
from policy_rate_monitor.chart.style import (
    LABEL_GAP,
    LINE_WIDTH,
    NO_DATA,
    alt_text,
    label_positions,
    line_style,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from pandas import DataFrame

    from policy_rate_monitor.core.models import DataSnapshot

__all__ = ["rate_figure"]


def rate_figure(
    title: str,
    snapshots: Sequence[DataSnapshot],
    observations: DataFrame,
    start: date | None = None,
) -> Figure:
    """Builds the step chart of the policy rates of the requested countries or areas.

    The n-th line gets ``line_style(n)``, as in the report's PNG, and is named at its
    right end. ``layout.meta.description`` holds the chart's text alternative.

    Args:
        title: The chart title, the dataset's title.
        snapshots: One snapshot per requested country or area; those with a latest
            rate get a line.
        observations: The staged observations.
        start: First date on the chart; ``None`` for the whole history.

    Returns:
        The figure, one trace per charted series, in request order.
    """
    figure = Figure()
    charted = charted_series(snapshots)
    ends: list[tuple[str, date, float, str]] = []
    for index, series in enumerate(charted):
        rows = valid_rows(observations, series.series_key, start)
        style = line_style(index)
        dates = rows["date"].tolist()
        # Floats only for drawing; every number in the API's JSON stays exact.
        values = [float(value) for value in rows["value"]]
        figure.add_trace(
            Scatter(
                x=dates,
                y=values,
                name=series.ref_area_name,
                mode="lines",
                line_shape="hv",
                line={
                    "color": style.colour,
                    "width": LINE_WIDTH,
                    "dash": _dash(style.dash),
                },
                hovertemplate=_hover(series.decimals),
            )
        )
        if values:
            ends += [(series.ref_area_name, dates[-1], values[-1], style.label_colour)]
    figure.update_layout(
        title=title,
        yaxis_title="Per cent",
        annotations=_end_labels(ends) if ends else [_no_data()],
        legend={"orientation": "h", "yanchor": "top", "y": -0.12},
        margin={"r": 140},
        meta={
            "description": alt_text(
                title, [series.ref_area_name for series in charted], start
            )
        },
    )
    return figure


def _dash(pattern: tuple[int, ...]) -> str:
    if not pattern:
        return "solid"
    return ",".join(f"{length}px" for length in pattern)


def _hover(decimals: int | None) -> str:
    rate = "%{y}" if decimals is None else f"%{{y:.{decimals}f}}"
    return f"%{{x|%Y-%m-%d}}: {rate}%"


def _end_labels(ends: list[tuple[str, date, float, str]]) -> list[dict[str, object]]:
    values = [value for _, _, value, _ in ends]
    gap = (max(values) - min(values) or 1.0) * LABEL_GAP
    positions = label_positions(values, gap)
    return [
        {
            "x": day,
            "y": position,
            "text": name,
            "showarrow": False,
            "xanchor": "left",
            "xshift": 6,
            "font": {"color": colour},
        }
        for (name, day, _, colour), position in zip(ends, positions, strict=True)
    ]


def _no_data() -> dict[str, object]:
    return {
        "text": NO_DATA,
        "x": 0.5,
        "y": 0.5,
        "xref": "paper",
        "yref": "paper",
        "showarrow": False,
    }
