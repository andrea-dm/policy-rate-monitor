"""The provider over the source's bulk-download file.

``fetch`` caches the ZIP, ``transform`` builds the staged files from it, and the
query methods read the staged files. The source release is the one staged with them
(``staged/release.json``), not the one of the cached ZIP, which a later ``fetch`` may
have replaced.

See also: [decision 0008](adr/0008-provider-interface.md) and [Data
model](data-model.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from pyarrow import ArrowException, Table

from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.fetch import fetch
from policy_rate_monitor.providers import (
    DataProvider,
    observation_schema,
    select_series,
)
from policy_rate_monitor.reference import NO_REFERENCE
from policy_rate_monitor.transform import (
    load_data_quality,
    load_observations,
    load_release,
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
        TransformSummary,
    )
    from policy_rate_monitor.reference import ReferenceSource

__all__ = ["BulkFileProvider"]


class BulkFileProvider(DataProvider):
    """Serves the data of one bulk-download file, cached under a data directory."""

    def __init__(
        self,
        config: DatasetConfig,
        data_dir: Path,
        get: Callable[[str], bytes] | None = None,
        reference: ReferenceSource = NO_REFERENCE,
    ) -> None:
        """Sets where the source is and where the files go.

        Args:
            config: The dataset configuration.
            data_dir: The data directory: ``raw/`` and ``staged/`` go under it.
            get: Downloads one URL and returns the body; ``None`` uses HTTP(S) with
                the configured timeouts. Tests pass a stand-in.
            reference: Where ``transform``'s SDMX rules read the reference; the
                default has none, so an enabled SDMX rule reports "not evaluated".
        """
        super().__init__()
        self._config = config
        self._data_dir = data_dir
        self._get = get
        self._reference = reference
        self._schema = observation_schema(config.structure)
        # series.json as last read, and the (mtime, size) of the file it was read from.
        self._series: tuple[tuple[int, int], dict[str, SeriesMetadata]] | None = None

    @override
    def fetch(self) -> SourceRelease:
        """Downloads the source ZIP unless a recent copy is cached (``fetch.fetch``)."""
        return fetch(self._config.source, self._data_dir, self._get)

    @override
    def transform(self) -> TransformSummary:
        """Builds the raw and staged layers from the cached ZIP (``transform``)."""
        return transform(self._config, self._data_dir, self._reference)

    @override
    def list_series(self) -> list[str]:
        """Returns the key of every staged series, from ``staged/series.json``."""
        return list(self._staged_series())

    @override
    def series_metadata(self, series_key: str) -> SeriesMetadata:
        """Returns the metadata of one staged series; ``KeyError`` if there is none."""
        series = self._staged_series()
        if series_key not in series:
            msg = f"no series {series_key!r}"
            raise KeyError(msg)
        return series[series_key]

    @override
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        """Reads ``staged/observations.parquet`` in the canonical schema.

        Raises:
            MissingInputError: Nothing is staged, or the staged observations do not
                match the configured schema (built with an older configuration).
        """
        frame = load_observations(self._data_dir)
        try:
            table = Table.from_pandas(frame, preserve_index=False).cast(self._schema)
        except (ValueError, ArrowException) as error:
            path = self._data_dir / "staged" / "observations.parquet"
            msg = f"{path} was staged with another configuration ({error})"
            raise MissingInputError(msg, step="transform") from error
        return select_series(table, series_keys)

    @override
    def data_quality_issues(self) -> list[DataQualityIssue]:
        """Reads ``staged/data_quality.json``."""
        return load_data_quality(self._data_dir)

    @override
    def source_release(self) -> SourceRelease | None:
        """Reads ``staged/release.json``; ``None`` when nothing staged records it."""
        return load_release(self._data_dir)

    def _staged_series(self) -> dict[str, SeriesMetadata]:
        """``staged/series.json`` by key, read again only when the file changes."""
        path = self._data_dir / "staged" / "series.json"
        try:
            stat = path.stat()
        except FileNotFoundError:
            msg = f"{path} does not exist"
            raise MissingInputError(msg, step="transform") from None
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._series
        if cached is None or cached[0] != stamp:
            series = {item.series_key: item for item in load_series(self._data_dir)}
            cached = (stamp, series)
            self._series = cached
        return cached[1]
