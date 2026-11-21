from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, override

import pytest
from pyarrow import Table, date32, decimal128

from policy_rate_monitor.providers import DataProvider, observation_schema
from tests.config.fixtures import changed, load, minimal_document
from tests.providers.contract import (
    check_one_row_per_series_and_date,
    check_sorted_by_date,
    check_validity,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        SeriesMetadata,
        SourceRelease,
    )

pytestmark = pytest.mark.unit

STRUCTURE = load(minimal_document()).structure


def table(*rows: tuple[str, date, str | None, bool | None]) -> Table:
    return Table.from_pylist(
        [
            {
                "series_key": key,
                "frq": "D",
                "geo": key.split(".")[1],
                "date": day,
                "value": None if value is None else Decimal(value),
                "sts": "A",
                "is_valid": valid,
            }
            for key, day, value, valid in rows
        ],
        schema=observation_schema(STRUCTURE),
    )


@pytest.mark.parametrize(
    ("check", "broken", "message"),
    [
        (
            check_one_row_per_series_and_date,
            table(
                ("D.AA", date(2026, 1, 1), "1", True),
                ("D.AA", date(2026, 1, 1), "2", True),
            ),
            "two rows",
        ),
        (
            check_sorted_by_date,
            table(
                ("D.AA", date(2026, 1, 2), "1", True),
                ("D.BB", date(2026, 1, 1), "1", True),
                ("D.AA", date(2026, 1, 1), "1", True),
            ),
            "comes after",
        ),
        (
            check_validity,
            table(("D.AA", date(2026, 1, 1), "1", None)),
            "is_valid is empty",
        ),
        (
            check_validity,
            table(("D.AA", date(2026, 1, 1), None, True)),
            "has no value",
        ),
    ],
)
def test_each_check_fails_on_a_table_that_breaks_it(
    check: Callable[[Table], None], broken: Table, message: str
) -> None:
    with pytest.raises(AssertionError, match=message):
        check(broken)


def test_a_provider_missing_a_method_cannot_be_created() -> None:
    class NoSourceRelease(DataProvider):
        @override
        def fetch(self) -> SourceRelease:
            raise NotImplementedError

        @override
        def transform(self) -> None:
            raise NotImplementedError

        @override
        def list_series(self) -> list[str]:
            raise NotImplementedError

        @override
        def series_metadata(self, series_key: str) -> SeriesMetadata:
            raise NotImplementedError

        @override
        def observations(self, series_keys: Sequence[str] | None = None) -> Table:
            raise NotImplementedError

        @override
        def data_quality_issues(self) -> list[DataQualityIssue]:
            raise NotImplementedError

    with pytest.raises(TypeError, match="source_release"):
        NoSourceRelease()  # pyright: ignore[reportAbstractUsage]


def test_schema_follows_the_structure() -> None:
    schema = observation_schema(STRUCTURE)

    assert schema.names == [
        "series_key",
        "frq",
        "geo",
        "date",
        "value",
        "sts",
        "is_valid",
    ]
    assert schema.field("date").type == date32()
    assert schema.field("value").type == decimal128(12, 4)
    assert schema.field("value").nullable
    assert not schema.field("is_valid").nullable


def test_an_extra_dimension_or_kept_attribute_adds_a_column() -> None:
    document = minimal_document()
    dimensions = [
        *document["structure"]["dimensions"],
        {"id": "SECTOR", "coded": True, "column": "sector"},
    ]
    document = changed(document, "structure.dimensions", dimensions)
    document = changed(document, "structure.selection", {"FRQ": "D", "SECTOR": "CB"})
    document = changed(document, "structure.observation_attributes[1].keep_as", "note")

    schema = observation_schema(load(document).structure)

    assert schema.names == [
        "series_key",
        "frq",
        "geo",
        "sector",
        "date",
        "value",
        "sts",
        "note",
        "is_valid",
    ]
