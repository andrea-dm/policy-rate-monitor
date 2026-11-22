"""The interface between the data and the rest of the package.

See also: [decision 0008](adr/0008-provider-interface.md) and
[Architecture](architecture.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyarrow import Table

    from policy_rate_monitor.core.models import (
        DataQualityIssue,
        SeriesMetadata,
        SourceRelease,
        TransformSummary,
    )

__all__ = ["DataProvider"]


class DataProvider(ABC):
    """Where the snapshot, the report and the CLI get their data from.

    A provider prepares the data with the two lifecycle methods, which change
    state, and serves it through the query methods, which only read. Settings
    fixed for a provider's lifetime (where the source is, where the data goes) are
    arguments of its constructor.

    The query methods raise ``MissingInputError`` when the data is not prepared yet,
    or was prepared for another configuration, with ``step`` naming the lifecycle
    method to run; ``source_release`` raises nothing and returns ``None`` when the
    release is unknown.
    ``observations`` returns a table in the schema of ``observation_schema``, with at
    most one row per series and date, sorted by date within each series. Why an Arrow
    table: [decision 0001](adr/0001-data-stack-pyarrow.md).
    """

    @abstractmethod
    def fetch(self) -> SourceRelease:
        """Gets the source data, or makes sure a recent copy is at hand.

        Returns:
            The release of the source data now at hand.

        Raises:
            SourceUnavailableError: The source cannot be reached and no copy is at
                hand.
            UnexpectedFormatError: The source data is not in the expected format.
        """

    @abstractmethod
    def transform(self) -> TransformSummary:
        """Builds the canonical data from the source data.

        Its data-quality findings are read with ``data_quality_issues``. A failure
        leaves the canonical data of the previous run as it was.

        Returns:
            The counts of the run: rows read, observations, series and findings.

        Raises:
            MissingInputError: The source data has not been fetched (``step`` is
                ``"fetch"``).
            UnexpectedFormatError: The source data is not in the expected format:
                it cannot be read or parsed, a required column is missing, or a
                series does not make valid metadata.
        """

    @abstractmethod
    def list_series(self) -> list[str]:
        """Returns the key of every series.

        Returns:
            The series keys, sorted, e.g. ``["D.GB", "D.US"]``.

        Raises:
            MissingInputError: The data is not prepared yet.
        """

    @abstractmethod
    def series_metadata(self, series_key: str) -> SeriesMetadata:
        """Returns the metadata of one series.

        Args:
            series_key: The series key, e.g. ``"D.US"``.

        Returns:
            The series' title, unit, decimals and other attributes.

        Raises:
            KeyError: There is no series with this key.
            MissingInputError: The data is not prepared yet.
        """

    @abstractmethod
    def observations(self, series_keys: Sequence[str] | None = None) -> Table:
        """Returns the observations of the given series, or of all series.

        Args:
            series_keys: The series to return; ``None`` returns every series.

        Returns:
            A table in the schema of ``observation_schema``, with at most one row per
            series and date, sorted by series and date. An unknown key adds no row.

        Raises:
            MissingInputError: The data is not prepared yet, or does not match the
                configured schema.
        """

    @abstractmethod
    def data_quality_issues(self) -> list[DataQualityIssue]:
        """Returns the data-quality findings of the last ``transform``.

        Returns:
            The findings; empty when the data had none.

        Raises:
            MissingInputError: The data is not prepared yet.
        """

    @abstractmethod
    def source_release(self) -> SourceRelease | None:
        """Returns the release the canonical data was built from.

        Returns:
            The release; ``None`` when the provider has no record of it (nothing is
            prepared yet, or it was prepared with no release record): the data can
            still be used, with its provenance unknown.
        """
