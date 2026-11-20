"""Latest policy rate, the rate a year earlier and the change, per requested area.

Pure calculation on the staged observations: no I/O.
"""

from __future__ import annotations

import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from policy_rate_monitor.core.models import (
    BASIS_POINTS_PER_PERCENT,
    DataSnapshot,
    SnapshotFlag,
    SnapshotStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from pandas import DataFrame

    from policy_rate_monitor.config.models import SnapshotSection
    from policy_rate_monitor.core.models import SeriesMetadata

__all__ = ["SnapshotArgs", "compute_snapshots", "months_before"]


@dataclass(frozen=True)
class SnapshotArgs:
    """The inputs of ``compute_snapshots``.

    Attributes:
        observations: Staged observations with ``date``, ``value`` and ``is_valid``
            columns and the reference-area column.
        series: Metadata of every series in *observations*.
        requested: Area codes as the user typed them; they are stripped and
            upper-cased, empty ones and repeats are dropped.
        settings: Look-back and staleness thresholds.
        area_aliases: Requested code to the code used in the data, e.g. EA to XM.
        ref_area_column: Name of the reference-area column in *observations*.
    """

    observations: DataFrame
    series: Sequence[SeriesMetadata]
    requested: Sequence[str]
    settings: SnapshotSection
    area_aliases: Mapping[str, str]
    ref_area_column: str


def months_before(day: date, months: int) -> date:
    """Returns the same calendar date *months* earlier, clamped to the month's end.

    Args:
        day: The date to go back from.
        months: Number of calendar months to go back.

    Returns:
        The earlier date; a day that does not exist in the target month becomes the
        last day of that month.

    Examples:
        >>> months_before(date(2026, 11, 10), 12)
        datetime.date(2025, 11, 10)
        >>> months_before(date(2024, 2, 29), 12)
        datetime.date(2023, 2, 28)
        >>> months_before(date(2026, 3, 31), 1)
        datetime.date(2026, 2, 28)
    """
    month_index = day.year * 12 + day.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def compute_snapshots(args: SnapshotArgs) -> list[DataSnapshot]:
    """Computes one snapshot per requested area, in request order.

    Args:
        args: The observations, the series metadata, the requested codes and the
            settings.

    Returns:
        The snapshots, one per distinct requested code.
    """
    observations = args.observations
    series_by_area = {item.ref_area: item for item in args.series}
    all_dates: list[date] = observations["date"].dropna().tolist()
    as_of = max(all_dates, default=None)
    valid = observations[observations.is_valid]

    snapshots: list[DataSnapshot] = []
    seen: set[str] = set()
    for given in args.requested:
        code = given.strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        ref_area = args.area_aliases.get(code, code)
        area_series = series_by_area.get(ref_area)
        if area_series is None:
            snapshot = DataSnapshot(
                requested_ref_area=given,
                ref_area=ref_area,
                status=SnapshotStatus.UNKNOWN_AREA,
            )
        else:
            rows = valid[valid[args.ref_area_column] == ref_area]
            snapshot = _area_snapshot(given, area_series, rows, args.settings, as_of)
        snapshots.append(snapshot)
    return snapshots


def _area_snapshot(
    given: str,
    series: SeriesMetadata,
    rows: DataFrame,
    settings: SnapshotSection,
    as_of: date | None,
) -> DataSnapshot:
    rows = rows.sort_values("date")
    dates: list[date] = rows["date"].tolist()
    values: list[Decimal] = rows["value"].tolist()
    if not dates:
        return DataSnapshot(
            requested_ref_area=given,
            ref_area=series.ref_area,
            series=series,
            status=SnapshotStatus.NO_VALID_DATA,
        )

    latest_date = dates[-1]
    latest_value = values[-1]
    target_date = months_before(latest_date, settings.lookback_months)
    flags: list[SnapshotFlag] = []
    if as_of is not None and (as_of - latest_date).days > settings.stale_latest_days:
        flags.append(SnapshotFlag.STALE_LATEST)

    prior_index = bisect_right(dates, target_date) - 1
    if prior_index < 0:
        return DataSnapshot(
            requested_ref_area=given,
            ref_area=series.ref_area,
            series=series,
            latest_date=latest_date,
            latest_value=latest_value,
            target_date=target_date,
            status=SnapshotStatus.INSUFFICIENT_HISTORY,
            flags=tuple(flags),
        )

    prior_date = dates[prior_index]
    prior_value = values[prior_index]
    if (target_date - prior_date).days > settings.stale_prior_days:
        flags.append(SnapshotFlag.STALE_PRIOR)
    return DataSnapshot(
        requested_ref_area=given,
        ref_area=series.ref_area,
        series=series,
        latest_date=latest_date,
        latest_value=latest_value,
        target_date=target_date,
        prior_date=prior_date,
        prior_value=prior_value,
        change_bps=(latest_value - prior_value) * BASIS_POINTS_PER_PERCENT,
        status=SnapshotStatus.OK,
        flags=tuple(flags),
    )
