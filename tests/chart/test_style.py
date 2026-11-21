"""The chart style: contrast, colour vision deficiencies, distinct lines and labels."""

from __future__ import annotations

import itertools
import math
from datetime import date

import pytest

from policy_rate_monitor.chart.style import (
    DASHES,
    LABEL_COLOURS,
    PALETTE,
    alt_text,
    label_positions,
    line_style,
)

pytestmark = pytest.mark.unit

# Machado, Oliveira and Fernandes (2009), full severity, applied to linear RGB.
SIMULATIONS = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def _linear(colour: str) -> list[float]:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    return [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]


def _contrast_on_white(colour: str) -> float:
    red, green, blue = _linear(colour)
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return 1.05 / (luminance + 0.05)


def _lab(rgb: list[float]) -> tuple[float, float, float]:
    rgb = [min(max(c, 0.0), 1.0) for c in rgb]
    x = (0.4124 * rgb[0] + 0.3576 * rgb[1] + 0.1805 * rgb[2]) / 0.95047
    y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    z = (0.0193 * rgb[0] + 0.1192 * rgb[1] + 0.9505 * rgb[2]) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _ciede2000(
    one: tuple[float, float, float], two: tuple[float, float, float]
) -> float:
    l1, a1, b1 = one
    l2, a2, b2 = two
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean**7 / (c_mean**7 + 25**7)))
    a1, a2 = (1 + g) * a1, (1 + g) * a2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    h1 = math.degrees(math.atan2(b1, a1)) % 360
    h2 = math.degrees(math.atan2(b2, a2)) % 360
    dh = 0.0 if c1 * c2 == 0 else (h2 - h1 + 180) % 360 - 180
    big_dh = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh / 2))
    l_mean, c_mean = (l1 + l2) / 2, (c1 + c2) / 2
    h_mean = h1 + h2
    if c1 * c2 != 0:
        h_mean = (h1 + h2) / 2 if abs(h1 - h2) <= 180 else (h1 + h2 + 360) / 2 % 360
    t = (
        1
        - 0.17 * math.cos(math.radians(h_mean - 30))
        + 0.24 * math.cos(math.radians(2 * h_mean))
        + 0.32 * math.cos(math.radians(3 * h_mean + 6))
        - 0.20 * math.cos(math.radians(4 * h_mean - 63))
    )
    rotation = -math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2))))
    rotation *= 2 * math.sqrt(c_mean**7 / (c_mean**7 + 25**7))
    s_l = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    s_c, s_h = 1 + 0.045 * c_mean, 1 + 0.015 * c_mean * t
    return math.sqrt(
        ((l2 - l1) / s_l) ** 2
        + ((c2 - c1) / s_c) ** 2
        + (big_dh / s_h) ** 2
        + rotation * ((c2 - c1) / s_c) * (big_dh / s_h)
    )


def _seen_with(colour: str, simulation: str) -> tuple[float, float, float]:
    rgb = _linear(colour)
    matrix = SIMULATIONS[simulation]
    return _lab([sum(m * c for m, c in zip(row, rgb, strict=True)) for row in matrix])


@pytest.mark.parametrize("colour", PALETTE)
def test_every_line_colour_has_3_to_1_on_white(colour: str) -> None:
    assert _contrast_on_white(colour) >= 3


@pytest.mark.parametrize("colour", LABEL_COLOURS)
def test_every_label_colour_has_4_5_to_1_on_white(colour: str) -> None:
    assert _contrast_on_white(colour) >= 4.5


@pytest.mark.parametrize("simulation", SIMULATIONS)
def test_the_colours_stay_apart_with_a_colour_vision_deficiency(
    simulation: str,
) -> None:
    # About 10 reads as clearly different; decision 0016 records 18.9 for this palette.
    distances = [
        _ciede2000(_seen_with(one, simulation), _seen_with(two, simulation))
        for one, two in itertools.combinations(PALETTE, 2)
    ]

    assert round(min(distances), 1) >= 18.9


def test_the_first_24_lines_all_differ_then_the_styles_repeat() -> None:
    count = len(PALETTE) * len(DASHES)
    styles = [line_style(index) for index in range(count + 1)]

    assert count == 24
    assert len({(style.colour, style.dash) for style in styles[:count]}) == count
    assert styles[count] == styles[0]


def test_a_line_changes_dash_once_the_colours_are_used() -> None:
    assert [line_style(index).dash for index in range(5)] == [(), (), (), (), (8, 4)]
    assert line_style(4).colour == PALETTE[0]


def test_labels_keep_a_gap_and_the_order_of_the_values() -> None:
    values = [3.875, 4.0, 2.5, 1.25, 0.0, 3.9]

    positions = label_positions(values, gap=0.3)

    in_order = sorted(range(len(values)), key=lambda index: values[index])
    placed = [positions[index] for index in in_order]
    assert all(
        above - below >= 0.3 - 1e-9 for below, above in itertools.pairwise(placed)
    )
    assert positions[4] == 0.0


def test_labels_far_enough_apart_stay_on_their_lines() -> None:
    assert label_positions([0.0, 2.5, 5.0], gap=1.0) == [0.0, 2.5, 5.0]


def test_the_text_alternative_names_the_chart_the_lines_and_the_period() -> None:
    text = alt_text(
        "Policy rates", ["United States", "Euro area", "Japan"], date(2015, 1, 1)
    )

    assert text == (
        "Policy rates: a step chart of United States, Euro area and Japan, in per cent,"
        " from 2015-01-01."
    )
    assert alt_text("Policy rates", ["Japan"]).endswith(
        "of Japan, in per cent, over the whole history."
    )
