"""A small dataset with every snapshot status, and providers to serve it."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import override

import pytest
from fastapi.testclient import TestClient
from pyarrow import Table

from policy_rate_monitor.config import DatasetConfig, FileConfigSource
from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.core.models import (
    SeriesMetadata,
    SourceRelease,
    TransformSummary,
)
from policy_rate_monitor.providers import InMemoryProvider
from policy_rate_monitor.web import create_app

CONFIG = Path(__file__).parents[2] / "configs" / "bis_cbpol.json"

# US and XM (asked for as EA) have a complete result, JP has no valid observation,
# CH has no observation a year before its latest one; UK is not in the data.
REQUESTED = ["US", "EA", "JP", "CH", "UK"]


def row(
    ref_area: str, day: date, value: str | None, *, valid: bool = True
) -> dict[str, object]:
    return {
        "series_key": f"D.{ref_area}",
        "freq": "D",
        "ref_area": ref_area,
        "date": day,
        "value": None if value is None else Decimal(value),
        "obs_status": "A",
        "is_valid": valid,
    }


ROWS = [
    row("US", date(2025, 1, 2), "4.5"),
    row("US", date(2025, 6, 2), "4.25", valid=False),
    row("US", date(2026, 1, 2), "3.75"),
    row("XM", date(2025, 1, 2), "3"),
    row("XM", date(2026, 1, 2), "2"),
    row("JP", date(2026, 1, 2), None, valid=False),
    row("CH", date(2025, 12, 1), "0.5"),
]
NAMES = {
    "US": "United States",
    "XM": "Euro area",
    "JP": "Japan",
    "CH": "Switzerland",
}
SERIES = [
    SeriesMetadata(
        series_key=f"D.{ref_area}",
        ref_area=ref_area,
        ref_area_name=name,
        frequency="D",
        title=f"Policy rate, {name}, daily",
    )
    for ref_area, name in NAMES.items()
]


@pytest.fixture(scope="session")
def config() -> DatasetConfig:
    return FileConfigSource(CONFIG).load()


@pytest.fixture
def provider(config: DatasetConfig) -> InMemoryProvider:
    return InMemoryProvider(config.structure, ROWS, SERIES)


@pytest.fixture
def client(
    config: DatasetConfig, provider: InMemoryProvider, tmp_path: Path
) -> TestClient:
    return TestClient(create_app(config, provider, tmp_path / "out"))


class StagedLater(InMemoryProvider):
    """Has nothing staged until ``transform`` runs, like a fresh data folder."""

    def __init__(self, config: DatasetConfig) -> None:
        super().__init__(config.structure, ROWS, SERIES)
        self.staged = False

    @override
    def transform(self) -> TransformSummary:
        self.staged = True
        return super().transform()

    @override
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        if not self.staged:
            msg = "data/staged/observations.parquet does not exist"
            raise MissingInputError(msg, step="transform")
        return super().observations(series_keys)


class Blocking(InMemoryProvider):
    """Its ``fetch`` waits until the test lets it finish."""

    def __init__(self, config: DatasetConfig) -> None:
        super().__init__(config.structure, ROWS, SERIES)
        self.release = threading.Event()
        self.started = threading.Event()

    @override
    def fetch(self) -> SourceRelease:
        self.started.set()
        self.release.wait(timeout=10)
        return super().fetch()


class SlowTransform(StagedLater):
    """Its ``transform`` waits until the test lets it finish."""

    def __init__(self, config: DatasetConfig) -> None:
        super().__init__(config)
        self.release = threading.Event()
        self.started = threading.Event()

    @override
    def transform(self) -> TransformSummary:
        self.started.set()
        self.release.wait(timeout=10)
        return super().transform()


class Growing(InMemoryProvider):
    """Serves one series fewer until ``transform`` runs, like a new release."""

    def __init__(self, config: DatasetConfig) -> None:
        super().__init__(config.structure, ROWS, SERIES)
        self.transformed = False

    @override
    def transform(self) -> TransformSummary:
        self.transformed = True
        return super().transform()

    @override
    def list_series(self) -> list[str]:
        keys = super().list_series()
        return keys if self.transformed else keys[:-1]
