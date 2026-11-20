"""The canonical form of the observations every provider returns.

See also: [Data model](data-model.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.compute as pc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from policy_rate_monitor.config.models import StructureSection

__all__ = ["observation_schema", "select_series"]


def observation_schema(structure: StructureSection) -> pa.Schema:
    """Returns the columns of the observations table, built from the configuration.

    The columns are, in order: ``series_key``; one per dimension, named by its
    ``column``; ``date``; ``value`` (a decimal with the measure's precision and
    scale, empty when the source has no number); one per observation attribute kept
    under a ``keep_as`` name; and ``is_valid``. Series attributes are not columns:
    they travel in ``SeriesMetadata``.

    Args:
        structure: The ``structure`` section of the configuration.

    Returns:
        The schema of the table ``DataProvider.observations`` returns.
    """
    measure = structure.measure
    dimensions = [
        pa.field(dimension.column, pa.string(), nullable=False)
        for dimension in structure.dimensions
    ]
    kept_attributes = [
        pa.field(attribute.keep_as, pa.string())
        for attribute in structure.observation_attributes
        if attribute.keep_as is not None
    ]
    return pa.schema(
        [
            pa.field("series_key", pa.string(), nullable=False),
            *dimensions,
            pa.field("date", pa.date32(), nullable=False),
            pa.field("value", pa.decimal128(measure.precision, measure.scale)),
            *kept_attributes,
            pa.field("is_valid", pa.bool_(), nullable=False),
        ]
    )


def select_series(table: pa.Table, series_keys: Sequence[str] | None) -> pa.Table:
    """Keeps the rows of the given series, in their order; ``None`` keeps all rows.

    Args:
        table: An observations table.
        series_keys: The series to keep, or ``None``.

    Returns:
        The rows of *table* whose ``series_key`` is in *series_keys*.
    """
    if series_keys is None:
        return table
    wanted = pa.array(series_keys, pa.string())
    # The stubs leave a parameter of is_in untyped.
    keep = pc.is_in(table["series_key"], value_set=wanted)  # pyright: ignore[reportUnknownMemberType]
    return table.filter(keep)
