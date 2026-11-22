"""The summary files: ``summary.csv`` and ``summary.json``.

One row per requested country or economic area, with its status, flags and the series
fields chosen in the configuration. The JSON document is also what the HTTP API's
``/summary`` returns. Both carry every rate exactly, as the source gives it.

See also: [Data model](data-model.md#outputs) and [decision
0005](adr/0005-exact-decimal-rates.md).
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from policy_rate_monitor.config.models import DatasetConfig, MetadataField
    from policy_rate_monitor.core.models import DataSnapshot, SourceRelease

__all__ = ["plain", "summary_csv", "summary_document"]


def plain(value: Decimal | None) -> str:
    """Writes a decimal exactly, without trailing zeros; ``None`` as an empty text.

    Args:
        value: The number.

    Returns:
        Its text.

    >>> from decimal import Decimal
    >>> plain(Decimal("4.000000"))
    '4'
    """
    if value is None:
        return ""
    return f"{value.normalize():f}"


def summary_csv(
    snapshots: Sequence[DataSnapshot],
    metadata_fields: Sequence[MetadataField],
) -> str:
    """Returns the content of ``summary.csv``: one row per snapshot, rates exact.

    Args:
        snapshots: One snapshot per requested country or area.
        metadata_fields: The series fields the configuration puts in the file.

    Returns:
        The CSV text, with a header row.
    """
    header = [
        "requested_ref_area",
        "ref_area",
        *metadata_fields,
        "latest_date",
        "latest_value",
        "prior_date",
        "prior_value",
        "change_bps",
        "status",
        "flags",
    ]
    file = io.StringIO()
    writer = csv.writer(file)
    writer.writerow(header)
    for snapshot in snapshots:
        metadata = [
            "" if snapshot.series is None else getattr(snapshot.series, field)
            for field in metadata_fields
        ]
        writer.writerow(
            [
                snapshot.requested_ref_area,
                snapshot.ref_area,
                *["" if value is None else str(value) for value in metadata],
                snapshot.latest_date or "",
                plain(snapshot.latest_value),
                snapshot.prior_date or "",
                plain(snapshot.prior_value),
                plain(snapshot.change_bps),
                snapshot.status,
                ";".join(snapshot.flags),
            ]
        )
    return file.getvalue()


def summary_document(
    config: DatasetConfig,
    snapshots: Sequence[DataSnapshot],
    release: SourceRelease | None,
    generated_at: datetime,
) -> dict[str, object]:
    """Returns the content of ``summary.json``, which the HTTP API also returns.

    Args:
        config: The dataset configuration.
        snapshots: One snapshot per requested country or area.
        release: Where and when the source file was retrieved; ``None`` if unknown.
        generated_at: When the summary was made.

    Returns:
        A JSON-ready dict: ``generated_at``, ``source``, ``dataset`` and
        ``snapshots``.
    """
    return {
        "generated_at": generated_at.isoformat(),
        "source": None if release is None else release.model_dump(mode="json"),
        "dataset": {"id": config.dataset.id, "title": config.dataset.title},
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
    }
