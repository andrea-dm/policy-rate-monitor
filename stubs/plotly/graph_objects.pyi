# The part of plotly.graph_objects that policy_rate_monitor.charts and report use (plotly ships no
# types).
from collections.abc import Sequence
from datetime import date
from typing import Any

class Scatter:
    def __init__(
        self,
        *,
        x: Sequence[date],
        y: Sequence[float],
        name: str,
        mode: str = ...,
        line_shape: str = ...,
    ) -> None: ...

class Figure:
    def __init__(self) -> None: ...
    def add_trace(self, trace: Scatter) -> Figure: ...
    def update_layout(self, **layout: Any) -> Figure: ...
    def to_html(
        self,
        *,
        full_html: bool = ...,
        include_plotlyjs: bool | str = ...,
        default_height: int | str = ...,
    ) -> str: ...
    def to_json(self) -> str: ...
    def to_plotly_json(self) -> dict[str, Any]: ...
