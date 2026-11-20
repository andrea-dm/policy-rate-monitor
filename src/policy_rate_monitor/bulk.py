"""The provider over the source's bulk-download file.

``fetch`` caches the ZIP, ``transform`` builds the staged files from it, and the
query methods read the staged files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from pyarrow import Table

from policy_rate_monitor.fetch import fetch, read_release
from policy_rate_monitor.providers import (
    DataProvider,
    observation_schema,
    select_series,
)
from policy_rate_monitor.transform import (
    load_data_quality,
    load_observations,
    load_series,
    transform,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from policy_rate_monitor.config.models import DatasetConfig
    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        SeriesMetadata,
        SourceRelease,
    )

__all__ = ["BulkFileProvider"]


class BulkFileProvider(DataProvider):
    """Serves the data of one bulk-download file, cached under a data directory."""

    def __init__(
        self,
        config: DatasetConfig,
        data_dir: Path,
        get: Callable[[str], bytes] | None = None,
    ) -> None:
        """Sets where the source is and where the files go.

        Args:
            config: The dataset configuration.
            data_dir: The data directory: ``raw/`` and ``staged/`` go under it.
            get: Downloads one URL and returns the body; ``None`` uses HTTP(S) with
                the configured timeouts. Tests pass a stand-in.
        """
        super().__init__()
        self._config = config
        self._data_dir = data_dir
        self._get = get
        self._schema = observation_schema(config.structure)

    @override
    def fetch(self) -> SourceRelease:
        return fetch(self._config.source, self._data_dir, self._get)

    @override
    def transform(self) -> None:
        transform(self._config, self._data_dir)

    @override
    def list_series(self) -> list[str]:
        return [item.series_key for item in load_series(self._data_dir)]

    @override
    def series_metadata(self, series_key: str) -> SeriesMetadata:
        for item in load_series(self._data_dir):
            if item.series_key == series_key:
                return item
        msg = f"no series {series_key!r}"
        raise KeyError(msg)

    @override
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        frame = load_observations(self._data_dir)
        table = Table.from_pandas(frame, preserve_index=False).cast(self._schema)
        return select_series(table, series_keys)

    @override
    def data_quality_issues(self) -> list[DataQualityIssue]:
        return load_data_quality(self._data_dir)

    @override
    def source_release(self) -> SourceRelease | None:
        return read_release(self._config.source, self._data_dir)
