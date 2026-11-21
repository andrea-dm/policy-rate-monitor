"""Latest policy rate, the rate a year earlier and the change, per country or area.

Pure calculation on the staged observations: no I/O.

See also: [How it works](how-it-works.md), sections 5 to 8, and
[decision 0006](adr/0006-twelve-month-look-back.md).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from policy_rate_monitor.analytics import (
    EPOCH,
    NO_INDEX,
    day_number,
    rate_history,
    snapshot_at,
)
from policy_rate_monitor.core.models import (
    BASIS_POINTS_PER_PERCENT,
    DataSnapshot,
    SnapshotFlag,
    SnapshotStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pandas import DataFrame

    from policy_rate_monitor.analytics import RateHistory, SnapshotArrays
    from policy_rate_monitor.config.models import SnapshotSection
    from policy_rate_monitor.core.models import SeriesMetadata

__all__ = ["SnapshotArgs", "compute_snapshots", "months_before"]


@dataclass(frozen=True)
class SnapshotArgs:
    """The inputs of ``compute_snapshots``.

    Attributes:
        observations: Staged observations with the canonical columns, values as
            Arrow decimals (``to_pandas(types_mapper=ArrowDtype)``, as the CLI
            does).
        series: Metadata of every series in *observations*.
        requested: The codes as the user typed them. They are matched after
            stripping and upper-casing, empty ones and repeats are dropped, and
            each snapshot keeps the code as typed, without the spaces.
        settings: Look-back and staleness thresholds.
        ref_area_aliases: Requested code to the code used in the data, e.g. EA to XM.
    """

    observations: DataFrame
    series: Sequence[SeriesMetadata]
    requested: Sequence[str]
    settings: SnapshotSection
    ref_area_aliases: Mapping[str, str]


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
    """Computes one snapshot per requested country or area, in request order.

    The searches run on the arrays of ``analytics``, built for the requested series
    only.

    Args:
        args: The observations, the series metadata, the requested codes and the
            settings.

    Returns:
        The snapshots, one per distinct requested code.

    Raises:
        TypeError: The values in *observations* are not Arrow decimals.
    """
    series_by_ref_area = {item.ref_area: item for item in args.series}
    requests: list[tuple[str, str, SeriesMetadata | None]] = []
    seen: set[str] = set()
    for given in args.requested:
        code = given.strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        ref_area = args.ref_area_aliases.get(code, code)
        requests.append((given, ref_area, series_by_ref_area.get(ref_area)))

    observations = args.observations
    keys = [series.series_key for _, _, series in requests if series is not None]
    history = rate_history(observations[observations.series_key.isin(keys)])
    # Staleness is measured from the last date in the whole file, valid or not.
    last_date: date = observations.date.max() if len(observations) else EPOCH
    arrays = snapshot_at(history, day_number(last_date), args.settings.lookback_months)

    snapshots: list[DataSnapshot] = []
    for given, ref_area, series in requests:
        if series is None:
            snapshot = DataSnapshot(
                requested_ref_area=given,
                ref_area=ref_area,
                status=SnapshotStatus.UNKNOWN_REF_AREA,
            )
        else:
            found = _found(history, arrays, series.series_key)
            snapshot = _ref_area_snapshot(
                given, series, found, args.settings, last_date
            )
        snapshots.append(snapshot)
    return snapshots


@dataclass(frozen=True)
class _Found:
    """The latest and prior observation the search found for one series."""

    history: RateHistory
    latest: int = NO_INDEX
    prior: int = NO_INDEX
    target_day: int = 0


def _found(history: RateHistory, arrays: SnapshotArrays, series_key: str) -> _Found:
    if series_key not in history.keys:
        return _Found(history)
    i = history.position(series_key)
    return _Found(
        history, int(arrays.latest[i]), int(arrays.prior[i]), int(arrays.target[i])
    )


def _ref_area_snapshot(
    given: str,
    series: SeriesMetadata,
    found: _Found,
    settings: SnapshotSection,
    last_date: date,
) -> DataSnapshot:
    if found.latest == NO_INDEX:
        return DataSnapshot(
            requested_ref_area=given,
            ref_area=series.ref_area,
            series=series,
            status=SnapshotStatus.NO_VALID_DATA,
        )

    history = found.history
    latest_date = history.date_at(found.latest)
    latest_value = history.value_at(found.latest)
    target_date = EPOCH + timedelta(days=found.target_day)
    flags: list[SnapshotFlag] = []
    if (last_date - latest_date).days > settings.stale_latest_days:
        flags.append(SnapshotFlag.STALE_LATEST)

    if found.prior == NO_INDEX:
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

    prior_date = history.date_at(found.prior)
    prior_value = history.value_at(found.prior)
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
