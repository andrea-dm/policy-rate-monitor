"""What every provider must do: the checks, and the suite that runs them.

A provider's test module subclasses ``ProviderContract`` in a class named ``Test...``
and supplies two fixtures: ``provider``, ready to query, and ``schema``, the
canonical schema of its configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from policy_rate_monitor.core.models import (
    DataQualityIssue,
    SourceRelease,
    TransformSummary,
)

if TYPE_CHECKING:
    from datetime import date

    from pyarrow import Schema, Table

    from policy_rate_monitor.providers import DataProvider


def check_schema(table: Table, schema: Schema) -> None:
    assert table.schema.equals(schema), f"\n{table.schema}\nis not\n{schema}"


def check_one_row_per_series_and_date(table: Table) -> None:
    seen: set[tuple[str, date]] = set()
    for row in _keys_and_dates(table):
        assert row not in seen, f"two rows for {row}"
        seen.add(row)


def check_sorted_by_date(table: Table) -> None:
    last_date: dict[str, date] = {}
    for key, day in _keys_and_dates(table):
        if key in last_date:
            assert day >= last_date[key], f"{key}: {day} comes after {last_date[key]}"
        last_date[key] = day


def check_validity(table: Table) -> None:
    is_valid = table["is_valid"].to_pylist()
    values = table["value"].to_pylist()
    assert None not in is_valid, "is_valid is empty on some rows"
    for valid, value in zip(is_valid, values, strict=True):
        assert not valid or value is not None, "a valid row has no value"


def _series_keys(table: Table) -> list[str]:
    return cast("list[str]", table["series_key"].to_pylist())


def _keys_and_dates(table: Table) -> list[tuple[str, date]]:
    dates = cast("list[date]", table["date"].to_pylist())
    return list(zip(_series_keys(table), dates, strict=True))


class ProviderContract:
    """The checks, run on one provider."""

    def test_observations_have_the_canonical_schema(
        self, provider: DataProvider, schema: Schema
    ) -> None:
        check_schema(provider.observations(), schema)

    def test_one_row_per_series_and_date(self, provider: DataProvider) -> None:
        check_one_row_per_series_and_date(provider.observations())

    def test_dates_are_sorted_within_each_series(self, provider: DataProvider) -> None:
        check_sorted_by_date(provider.observations())

    def test_validity_is_set_and_valid_rows_have_a_value(
        self, provider: DataProvider
    ) -> None:
        check_validity(provider.observations())

    def test_every_series_has_its_metadata(self, provider: DataProvider) -> None:
        listed = provider.list_series()
        for key in set(_series_keys(provider.observations())):
            assert key in listed
            assert provider.series_metadata(key).series_key == key

    def test_observations_of_one_series(self, provider: DataProvider) -> None:
        first = provider.list_series()[0]
        keys = _series_keys(provider.observations([first]))
        assert keys
        assert set(keys) == {first}

    def test_fetch_and_source_release_return_a_release(
        self, provider: DataProvider
    ) -> None:
        assert isinstance(provider.fetch(), SourceRelease)
        assert isinstance(provider.source_release(), SourceRelease)

    def test_transform_returns_the_counts_it_staged(
        self, provider: DataProvider
    ) -> None:
        summary = provider.transform()

        assert isinstance(summary, TransformSummary)
        assert summary.observations == provider.observations().num_rows
        assert summary.series == len(provider.list_series())
        assert summary.issues == len(provider.data_quality_issues())
        assert summary.rows_read >= summary.observations

    def test_data_quality_issues_are_core_models(self, provider: DataProvider) -> None:
        issues = provider.data_quality_issues()
        assert all(isinstance(issue, DataQualityIssue) for issue in issues)
