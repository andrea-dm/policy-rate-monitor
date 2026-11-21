"""The look of the policy-rate charts, shared by the PNG and the Plotly figure.

Nothing here imports a plotting library, so the report's PNG (matplotlib, base install)
and the interactive chart (Plotly, the ``interactive`` extra) draw the n-th line with
the same colour and dash, label the line ends the same way and share one text
alternative.

See also: [How it works](how-it-works.md), section 10, and [decision
0016](adr/0016-accessible-charts.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

__all__ = [
    "DASHES",
    "LABEL_COLOURS",
    "LABEL_GAP",
    "LINE_WIDTH",
    "NO_DATA",
    "PALETTE",
    "LineStyle",
    "alt_text",
    "label_positions",
    "line_style",
]

PALETTE: Final = ("#006BA4", "#C85200", "#898989", "#595959")
"""The line colours: matplotlib's tableau-colorblind10 at 3:1 or more on white."""

LABEL_COLOURS: Final = ("#006BA4", "#C85200", "#767676", "#595959")
"""The colour of each line's label: its line's colour, darkened to 4.5:1 for text."""

DASHES: Final[tuple[tuple[int, ...], ...]] = (
    (),  # solid
    (8, 4),  # dash
    (2, 3),  # dot
    (8, 3, 2, 3),  # dash-dot
    (16, 4),  # long dash
    (16, 4, 2, 4),  # long dash-dot
)
"""The dash patterns, as on-off lengths in pixels; ``()`` is a solid line."""

LINE_WIDTH: Final = 2
"""The width of a line, in pixels."""

NO_DATA: Final = "No data to plot"
"""What a chart with no line says."""

LABEL_GAP: Final = 0.05
"""The smallest gap between two line-end labels: a share of the y axis (PNG) or of
the spread of the latest rates (Plotly, whose axis is set in the browser)."""


@dataclass(frozen=True)
class LineStyle:
    """How one line is drawn.

    Attributes:
        colour: The colour, as ``#RRGGBB``.
        dash: The dash pattern, as on-off lengths in pixels; ``()`` is a solid line.
        label_colour: The colour of the line's name at its end, as ``#RRGGBB``.
    """

    colour: str
    dash: tuple[int, ...]
    label_colour: str


def line_style(index: int) -> LineStyle:
    """Returns the colour and dash of the line at *index* (0 for the first line).

    The lines take the colours in turn, and the next dash pattern each time the colours
    run out, so the first ``len(PALETTE) * len(DASHES)`` lines all differ.

    Args:
        index: The position of the line on the chart, from 0.

    Returns:
        Its colour and dash.

    >>> line_style(0)
    LineStyle(colour='#006BA4', dash=(), label_colour='#006BA4')
    >>> line_style(6)
    LineStyle(colour='#898989', dash=(8, 4), label_colour='#767676')
    """
    colour = index % len(PALETTE)
    dash = DASHES[index // len(PALETTE) % len(DASHES)]
    return LineStyle(
        colour=PALETTE[colour], dash=dash, label_colour=LABEL_COLOURS[colour]
    )


def alt_text(title: str, names: Sequence[str], start: date | None = None) -> str:
    """Returns the text alternative of a chart: its type, its lines and its period.

    Args:
        title: The chart title, the dataset's title.
        names: The name of each line, in the order drawn.
        start: First date on the chart; ``None`` for the whole history.

    Returns:
        One sentence.

    >>> from datetime import date
    >>> alt_text("Rates", ["United States", "Euro area"], date(2015, 1, 1))
    'Rates: a step chart of United States and Euro area, in per cent, from 2015-01-01.'
    >>> alt_text("Rates", [])
    'Rates: no country or economic area has a rate to plot.'
    """
    if not names:
        return f"{title}: no country or economic area has a rate to plot."
    lines = names[0]
    if len(names) > 1:
        lines = ", ".join(names[:-1]) + " and " + names[-1]
    period = "over the whole history" if start is None else f"from {start}"
    return f"{title}: a step chart of {lines}, in per cent, {period}."


def label_positions(values: Sequence[float], gap: float) -> list[float]:
    """Returns where to write the labels of the line ends, so that none overlap.

    Each label starts at its line's value. Going up from the lowest, a label closer
    than *gap* to the one below it is moved up to *gap* above it, so the labels keep
    the order of the values.

    Args:
        values: The last value of each line.
        gap: The smallest distance between two labels, in the units of the values.

    Returns:
        One position per value, in the order of *values*.

    >>> label_positions([4.0, 0.0, 3.9], gap=0.25)
    [4.15, 0.0, 3.9]
    """
    positions = list(values)
    order = sorted(range(len(values)), key=lambda index: values[index])
    for below, above in pairwise(order):
        positions[above] = max(positions[above], positions[below] + gap)
    return positions
