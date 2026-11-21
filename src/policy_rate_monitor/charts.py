"""The interactive chart of the policy rates, as a Plotly figure.

It draws the same series and the same rows as the report's ``policy_rates.png``
(``charted_series`` and ``valid_rows`` in ``report``). The HTTP API returns it as
JSON for the web front end; it needs the ``interactive`` extra (Plotly).

See also: [HTTP API](api.md) and [decision
0002](adr/0002-charting-and-report-format.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plotly.graph_objects import Figure, Scatter

from policy_rate_monitor.report import charted_series, valid_rows

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
    for series in charted_series(snapshots):
        rows = valid_rows(observations, series.series_key, start)
        # Floats only for drawing; every number in the API's JSON stays exact.
        figure.add_trace(
            Scatter(
                x=rows["date"].tolist(),
                y=[float(value) for value in rows["value"]],
                name=series.ref_area_name,
                mode="lines",
                line_shape="hv",
            )
        )
    figure.update_layout(title=title, yaxis_title="Per cent")
    return figure
