from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pyarrow import Schema

from policy_rate_monitor.core.models import (
    DataQualityIssue,
    DataQualitySeverity,
    SeriesMetadata,
)
from policy_rate_monitor.providers import InMemoryProvider, observation_schema
from tests.config.fixtures import load, minimal_document
from tests.providers.contract import ProviderContract

pytestmark = pytest.mark.unit

STRUCTURE = load(minimal_document()).structure


def row(key: str, day: date, value: str | None, status: str = "A") -> dict[str, object]:
    return {
        "series_key": key,
        "frq": "D",
        "geo": key.split(".")[1],
        "date": day,
        "value": None if value is None else Decimal(value),
        "sts": status,
        "is_valid": value is not None,
    }


def series(key: str) -> SeriesMetadata:
    ref_area = key.split(".")[1]
    return SeriesMetadata(
        series_key=key,
        ref_area=ref_area,
        ref_area_name=f"Name of {ref_area}",
        frequency="D",
        title=f"Rate of {ref_area}",
    )


@pytest.fixture
def provider() -> InMemoryProvider:
    # Given out of order; one series reaches back before 1970, the other has gaps.
    rows = [
        row("D.BB", date(2026, 1, 3), "1.5"),
        row("D.BB", date(2026, 1, 2), None, status="X"),
        row("D.AA", date(1970, 1, 2), "6"),
        row("D.AA", date(1965, 3, 1), "4.25"),
        row("D.AA", date(1969, 12, 31), "-0.5"),
        row("D.BB", date(2026, 1, 1), "1.25"),
    ]
    issue = DataQualityIssue(
        rule="exact_duplicate",
        severity=DataQualitySeverity.INFO,
        detail="rows repeated exactly; the copies were dropped",
        series_key="D.BB",
        count=1,
    )
    return InMemoryProvider(
        STRUCTURE, rows, [series("D.AA"), series("D.BB")], issues=[issue]
    )


@pytest.fixture
def schema() -> Schema:
    return observation_schema(STRUCTURE)


class TestInMemoryProvider(ProviderContract):
    pass


def test_rows_are_sorted_by_series_and_date(provider: InMemoryProvider) -> None:
    table = provider.observations()

    assert table["series_key"].to_pylist() == ["D.AA"] * 3 + ["D.BB"] * 3
    assert table["date"].to_pylist()[:3] == [
        date(1965, 3, 1),
        date(1969, 12, 31),
        date(1970, 1, 2),
    ]
    assert table["value"].to_pylist()[4] is None
