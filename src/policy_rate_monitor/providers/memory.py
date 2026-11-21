"""A provider that holds a few observations in memory, for tests.

See also: [decision 0008](adr/0008-provider-interface.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

from pyarrow import Table

from policy_rate_monitor.core.models import SourceRelease
from policy_rate_monitor.providers.base import DataProvider
from policy_rate_monitor.providers.schema import observation_schema, select_series

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from policy_rate_monitor.config.models import StructureSection
    from policy_rate_monitor.core.models import DataQualityIssue, SeriesMetadata

__all__ = ["InMemoryProvider"]

_RELEASE = SourceRelease(
    location="memory",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)


class InMemoryProvider(DataProvider):
    """Canonical data given as plain rows; ``fetch`` and ``transform`` do nothing.

    The snapshot and the report can be tested on it without a source file, and the
    planned HTTP API will be too.
    """

    def __init__(
        self,
        structure: StructureSection,
        rows: Iterable[Mapping[str, object]],
        series: Sequence[SeriesMetadata],
        issues: Sequence[DataQualityIssue] = (),
        release: SourceRelease = _RELEASE,
    ) -> None:
        """Keeps the rows as a table in the canonical schema, sorted by series and date.

        Args:
            structure: The ``structure`` section the rows follow.
            rows: One mapping per observation, keyed by column name.
            series: The metadata of every series in *rows*.
            issues: The data-quality findings to report.
            release: The release ``fetch`` and ``source_release`` return.
        """
        super().__init__()
        table = Table.from_pylist(list(rows), schema=observation_schema(structure))
        # The stubs leave a parameter of sort_by untyped.
        self._observations = table.sort_by(  # pyright: ignore[reportUnknownMemberType]
            [("series_key", "ascending"), ("date", "ascending")]
        )
        self._series = {item.series_key: item for item in series}
        self._issues = list(issues)
        self._release = release

    @override
    def fetch(self) -> SourceRelease:
        """Returns the release given to the constructor; nothing is downloaded."""
        return self._release

    @override
    def transform(self) -> None:
        """Does nothing: the rows are already canonical."""

    @override
    def list_series(self) -> list[str]:
        """Returns the keys of the series given, sorted."""
        return sorted(self._series)

    @override
    def series_metadata(self, series_key: str) -> SeriesMetadata:
        """Returns the metadata given for one series; ``KeyError`` if there is none."""
        return self._series[series_key]

    @override
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        """Returns the rows given, as a table in the canonical schema."""
        return select_series(self._observations, series_keys)

    @override
    def data_quality_issues(self) -> list[DataQualityIssue]:
        """Returns the findings given to the constructor."""
        return list(self._issues)

    @override
    def source_release(self) -> SourceRelease | None:
        """Returns the release given to the constructor."""
        return self._release
