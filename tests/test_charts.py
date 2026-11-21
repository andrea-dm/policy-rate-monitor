"""The interactive chart: the same series and rows as the report's chart."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from policy_rate_monitor.charts import rate_figure
from policy_rate_monitor.config import DatasetConfig, FileConfigSource
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.service import load_staged, summary
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
    assert trace["line"] == {"shape": "hv"}


def test_the_chart_starts_at_start(config: DatasetConfig) -> None:
    (trace,) = _traces(config, ["US"], start=date(2025, 6, 1))

    assert list(trace["x"]) == [date(2026, 1, 2)]


def test_the_title_and_the_axis_come_from_the_caller(config: DatasetConfig) -> None:
    data = load_staged(InMemoryProvider(config.structure, ROWS, SERIES))
    figure = rate_figure("A title", summary(config, data, ["US"]), data.observations)

    layout = figure.to_plotly_json()["layout"]
    assert layout["title"]["text"] == "A title"
    assert layout["yaxis"]["title"]["text"] == "Per cent"
