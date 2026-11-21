"""The interactive chart: the same series and rows as the report's chart."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from policy_rate_monitor.chart_style import NO_DATA, alt_text, line_style
from policy_rate_monitor.charts import rate_figure
from policy_rate_monitor.config import DatasetConfig, FileConfigSource
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.service import load_staged, summary
from tests.test_report import _full_args
from tests.web.conftest import CONFIG, REQUESTED, ROWS, SERIES

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return FileConfigSource(CONFIG).load()


def _traces(
    config: DatasetConfig, requested: list[str], start: date | None = None
) -> list[dict[str, Any]]:
    data = load_staged(InMemoryProvider(config.structure, ROWS, SERIES))
    snapshots = summary(config, data, requested)
    figure = rate_figure("Policy rates", snapshots, data.observations, start)
    return figure.to_plotly_json()["data"]


def test_each_ref_area_with_a_latest_rate_gets_one_line_in_request_order(
    config: DatasetConfig,
) -> None:
    traces = _traces(config, ["CH", *REQUESTED])

    assert [trace["name"] for trace in traces] == [
        "Switzerland",
        "United States",
        "Euro area",
    ]


def test_a_line_has_the_valid_rates_only_as_steps(config: DatasetConfig) -> None:
    (trace,) = _traces(config, ["US"])

    assert list(trace["x"]) == [date(2025, 1, 2), date(2026, 1, 2)]
    assert list(trace["y"]) == [4.5, 3.75]
    assert trace["line"]["shape"] == "hv"


def test_the_chart_starts_at_start(config: DatasetConfig) -> None:
    (trace,) = _traces(config, ["US"], start=date(2025, 6, 1))

    assert list(trace["x"]) == [date(2026, 1, 2)]


def test_the_title_and_the_axis_come_from_the_caller(config: DatasetConfig) -> None:
    data = load_staged(InMemoryProvider(config.structure, ROWS, SERIES))
    figure = rate_figure("A title", summary(config, data, ["US"]), data.observations)

    layout = figure.to_plotly_json()["layout"]
    assert layout["title"]["text"] == "A title"
    assert layout["yaxis"]["title"]["text"] == "Per cent"


def _layout(config: DatasetConfig, requested: list[str]) -> dict[str, Any]:
    data = load_staged(InMemoryProvider(config.structure, ROWS, SERIES))
    figure = rate_figure(
        "Policy rates", summary(config, data, requested), data.observations
    )
    return figure.to_plotly_json()["layout"]


def test_the_n_th_line_has_the_n_th_line_style(config: DatasetConfig) -> None:
    traces = _traces(config, ["CH", "US", "EA"])

    for index, trace in enumerate(traces):
        style = line_style(index)
        assert trace["line"]["color"] == style.colour
        assert trace["line"]["width"] == 2
        expected = ",".join(f"{length}px" for length in style.dash) or "solid"
        assert trace["line"]["dash"] == expected


def test_each_line_is_named_at_its_end(config: DatasetConfig) -> None:
    layout = _layout(config, ["US", "EA"])

    labels = layout["annotations"]
    assert [label["text"] for label in labels] == ["United States", "Euro area"]
    assert [label["x"] for label in labels] == [date(2026, 1, 2), date(2026, 1, 2)]
    assert [label["font"]["color"] for label in labels] == [
        line_style(0).label_colour,
        line_style(1).label_colour,
    ]
    assert all(label["xanchor"] == "left" for label in labels)


def test_a_chart_with_no_line_says_so(config: DatasetConfig) -> None:
    layout = _layout(config, ["UK"])

    (label,) = layout["annotations"]
    assert label["text"] == NO_DATA
    assert (label["xref"], label["yref"]) == ("paper", "paper")


def test_the_figure_carries_its_text_alternative(config: DatasetConfig) -> None:
    layout = _layout(config, ["US", "EA", "UK"])

    assert layout["meta"]["description"] == alt_text(
        "Policy rates", ["United States", "Euro area"]
    )


def test_the_hover_shows_the_rate_with_the_series_decimals(tmp_path: Path) -> None:
    args = _full_args(tmp_path)
    figure = rate_figure("Rates", args.snapshots, args.observations)

    (trace,) = figure.to_plotly_json()["data"]
    assert trace["hovertemplate"] == "%{x|%Y-%m-%d}: %{y:.2f}%"
