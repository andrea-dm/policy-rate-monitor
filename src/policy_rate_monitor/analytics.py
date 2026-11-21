"""Fast, exact analytics on the staged observations.

The staged table is turned once into plain NumPy arrays (``RateHistory``); after
that every question is integer work on those arrays:

- dates are int32 days since 1970-01-01;
- values are int64 in units of 10**-scale percent (``Decimal("4.25")`` at scale 6 is
  ``4_250_000``), so differences stay exact. ``bps()`` turns a difference back into
  basis points;
- the valid observations of all series sit end to end, sorted by series and date;
  series ``i`` is ``days[offsets[i]:offsets[i + 1]]``;
- ``series_days`` packs the series position and the date into one int64
  (``i * 2**32 + day``), which rises across all series, so one binary search
  (``np.searchsorted``) answers a question for every series at once.

Each of the two searches has a NumPy version and a plain loop. When the optional
``numba`` package is installed (the ``fast`` extra) the loops are compiled with
``numba.njit`` and used instead. They compile on their first call, about 0.7 s,
and the compiled code is cached on disk for later processes. Both versions give the
same arrays. ``rate_moves`` needs no search and runs on NumPy only.

The arrays of a ``RateHistory`` are read-only, so a history can be shared by every
request of a server.

See also: [decision 0013](adr/0013-fast-analytics-numpy-numba.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
from pandas import ArrowDtype, Categorical, DataFrame, Series
from pyarrow import Decimal128Type

from policy_rate_monitor.config.models import CANONICAL_COLUMNS
from policy_rate_monitor.core.models import BASIS_POINTS_PER_PERCENT

if TYPE_CHECKING:
    from numpy.typing import NDArray

try:
    from numba import njit
except ImportError:  # numba is the optional "fast" extra
    njit = None

__all__ = [
    "EPOCH",
    "NO_INDEX",
    "NUMBA_AVAILABLE",
    "ChangeHistory",
    "RateHistory",
    "RateMoves",
    "SnapshotArrays",
    "bps",
    "change_history",
    "day_number",
    "months_back",
    "rate_history",
    "rate_moves",
    "snapshot_at",
]

NUMBA_AVAILABLE = njit is not None
"""Whether the kernels run compiled by numba (True) or with NumPy (False)."""

NO_INDEX = -1
"""Index returned when a series has no observation that qualifies."""

SERIES_KEY, DATE, VALUE, IS_VALID = CANONICAL_COLUMNS
"""Names of the canonical columns the arrays are built from."""
SERIES_STEP = 2**32
"""Distance between two series in ``series_days``; any int32 day fits between."""
EPOCH = date(1970, 1, 1)
"""Day 0 of the day numbers."""
DAY = np.dtype("datetime64[D]")
"""NumPy dtype of a date counted in days, for the calendar arithmetic."""
MONTH = np.dtype("datetime64[M]")
"""NumPy dtype of a date counted in months, for the calendar arithmetic."""
ONE_DAY = np.timedelta64(1, "D")
"""One day, to step NumPy day dates."""
ONE_MONTH = np.timedelta64(1, "M")
"""One month, to step NumPy month dates."""


@dataclass(frozen=True)
class RateHistory:
    """The valid observations of every series as fixed-point NumPy arrays.

    Attributes:
        keys: Series keys, sorted; series ``i`` is ``keys[i]``.
        offsets: ``len(keys) + 1`` positions; series ``i`` holds the observations
            ``offsets[i]`` to ``offsets[i + 1] - 1``. A series with no valid
            observation is kept, with no rows.
        days: Date of each observation, in days since 1970-01-01, rising within a
            series.
        series_days: ``i * SERIES_STEP + days`` for the observations of series
            ``i``; rising over the whole array.
        values: Value of each observation in units of 10**-scale percent.
        scale: Decimal places of the staged values.
    """

    keys: tuple[str, ...]
    offsets: NDArray[np.int64]
    days: NDArray[np.int32]
    series_days: NDArray[np.int64]
    values: NDArray[np.int64]
    scale: int

    def position(self, series_key: str) -> int:
        """Returns the position of a series in ``keys``.

        Args:
            series_key: The series key, e.g. ``"D.US"``.

        Returns:
            The position ``i`` of the series: its rows are ``offsets[i]`` to
            ``offsets[i + 1] - 1``.

        Raises:
            KeyError: The series is not in the history.
        """
        try:
            return self.keys.index(series_key)
        except ValueError:
            raise KeyError(series_key) from None

    def date_at(self, index: int) -> date:
        """Returns the date of one observation.

        Args:
            index: Position of the observation in the arrays.

        Returns:
            The observation date.
        """
        return EPOCH + timedelta(days=int(self.days[index]))

    def value_at(self, index: int) -> Decimal:
        """Returns the value of one observation, exactly as it was staged.

        Args:
            index: Position of the observation in the arrays.

        Returns:
            The value in percent, with the staged number of decimal places.
        """
        return Decimal(int(self.values[index])).scaleb(-self.scale)


@dataclass(frozen=True)
class ChangeHistory:
    """The change of every observation against the one *months* earlier.

    Attributes:
        prior: Index of the most recent observation of the same series on or before
            the same calendar date *months* earlier; ``NO_INDEX`` if there is none.
        change: ``values - values[prior]``, in units of 10**-scale percent; 0 where
            there is no prior observation.
    """

    prior: NDArray[np.int64]
    change: NDArray[np.int64]


@dataclass(frozen=True)
class SnapshotArrays:
    """Latest and prior observation of every series at one as-of date.

    Each array has one item per series, in the order of ``RateHistory.keys``.

    Attributes:
        latest: Index of the series' last observation on or before the as-of date;
            ``NO_INDEX`` if it has none.
        target: The latest observation's date *months* earlier, in days since
            1970-01-01; meaningless where ``latest`` is ``NO_INDEX``.
        prior: Index of the last observation on or before ``target``; ``NO_INDEX``
            if there is none.
        change: ``values[latest] - values[prior]``, in units of 10**-scale percent;
            0 where either index is ``NO_INDEX``.
    """

    latest: NDArray[np.int64]
    target: NDArray[np.int32]
    prior: NDArray[np.int64]
    change: NDArray[np.int64]


@dataclass(frozen=True)
class RateMoves:
    """The observations at which a series' rate differs from the one before.

    Attributes:
        index: Index of each observation whose value differs from the previous
            observation of the same series, in series and date order.
        change: The size of each move, in units of 10**-scale percent.
    """

    index: NDArray[np.int64]
    change: NDArray[np.int64]


def rate_history(observations: DataFrame) -> RateHistory:
    """Builds the fixed-point arrays from the staged observations.

    Only valid observations are kept.

    Args:
        observations: Staged observations with the canonical columns, values as
            Arrow decimals (as ``load_observations`` returns them).

    Returns:
        The arrays, series sorted by key and dates rising within each series.

    Raises:
        TypeError: The values are not Arrow decimals.
    """
    scale = _scale(observations[VALUE])
    keys = tuple(sorted(str(key) for key in observations[SERIES_KEY].unique()))
    valid = observations[observations[IS_VALID]]
    series = Categorical(valid[SERIES_KEY], categories=keys).codes
    days = valid[DATE].astype("int32[pyarrow]").to_numpy().astype(np.int32)
    # Exact: the staged values have exactly `scale` decimal places.
    scaled = (valid[VALUE] * 10**scale).astype("int64[pyarrow]")
    values = scaled.to_numpy().astype(np.int64)

    order = np.lexsort((days, series))
    days = days[order]
    series_days = series[order].astype(np.int64) * SERIES_STEP + days
    offsets = np.zeros(len(keys) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(np.bincount(series, minlength=len(keys)))
    values = values[order]
    for array in (offsets, days, series_days, values):
        array.flags.writeable = False
    return RateHistory(keys, offsets, days, series_days, values, scale)


def _scale(values: Series) -> int:
    dtype = values.dtype
    if isinstance(dtype, ArrowDtype):
        value_type = dtype.pyarrow_dtype
        if isinstance(value_type, Decimal128Type):
            # The stubs leave the scale untyped.
            return int(value_type.scale)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    msg = f"values should be Arrow decimals, got {dtype}"
    raise TypeError(msg)


def day_number(day: date) -> int:
    """Returns a date as days since 1970-01-01.

    Args:
        day: The date.

    Returns:
        The number of days from 1970-01-01, negative before it.

    Examples:
        >>> day_number(date(1970, 1, 2)), day_number(date(1969, 12, 31))
        (1, -1)
    """
    return (day - EPOCH).days


def months_back(days: NDArray[np.int32], months: int) -> NDArray[np.int32]:
    """Returns each date *months* calendar months earlier, clamped to the month end.

    The same rule as ``snapshot.months_before``, for a whole array at once.

    Args:
        days: Dates in days since 1970-01-01.
        months: Number of calendar months to go back.

    Returns:
        The earlier dates, in days since 1970-01-01.

    Examples:
        >>> leap_day = np.array([day_number(date(2024, 2, 29))], dtype=np.int32)
        >>> EPOCH + timedelta(days=int(months_back(leap_day, 12)[0]))
        datetime.date(2023, 2, 28)
    """
    day = days.astype(DAY)
    month = day.astype(MONTH)
    day_in_month = day - month.astype(DAY)
    target_month = month - months * ONE_MONTH
    month_end = (target_month + ONE_MONTH).astype(DAY) - ONE_DAY
    target = np.minimum(target_month.astype(DAY) + day_in_month, month_end)
    return target.astype(np.int32)


def bps(change: int | np.int64, scale: int) -> Decimal:
    """Turns a change in units of 10**-scale percent into basis points, exactly.

    Args:
        change: A difference of two values of the arrays.
        scale: Decimal places of the values (``RateHistory.scale``).

    Returns:
        The change in basis points.

    Examples:
        >>> bps(250_000, 6)
        Decimal('25.000000')
    """
    return Decimal(int(change)).scaleb(-scale) * BASIS_POINTS_PER_PERCENT


def change_history(history: RateHistory, months: int) -> ChangeHistory:
    """Computes, for every observation, its change against *months* earlier.

    The prior observation is the most recent one of the same series on or before
    the same calendar date *months* earlier, as for the snapshots.

    Args:
        history: The fixed-point arrays.
        months: Number of calendar months to look back.

    Returns:
        The prior index and the change of every observation.
    """
    targets = months_back(history.days, months)
    prior = _prior_index(history.series_days, history.offsets, targets)
    found = prior >= 0
    change = np.zeros(len(prior), dtype=np.int64)
    change[found] = history.values[found] - history.values[prior[found]]
    return ChangeHistory(prior, change)


def snapshot_at(history: RateHistory, as_of: int, months: int) -> SnapshotArrays:
    """Finds the latest and prior observation of every series at one as-of date.

    The latest observation is the last one on or before *as_of*; the prior one the
    last on or before the latest date *months* calendar months earlier.

    Args:
        history: The fixed-point arrays.
        as_of: The as-of date, in days since 1970-01-01 (see ``day_number``).
        months: Number of calendar months to look back from each latest date.

    Returns:
        One latest index, target date, prior index and change per series.
    """
    as_of_days = np.full(len(history.keys), as_of, dtype=np.int32)
    latest = _last_on_or_before(history.series_days, history.offsets, as_of_days)
    has_latest = latest >= 0
    latest_days = as_of_days.copy()
    latest_days[has_latest] = history.days[latest[has_latest]]
    target = months_back(latest_days, months)
    prior = _last_on_or_before(history.series_days, history.offsets, target)
    found = prior >= 0  # implies a latest observation: target <= latest date
    change = np.zeros(len(prior), dtype=np.int64)
    change[found] = history.values[latest[found]] - history.values[prior[found]]
    return SnapshotArrays(latest, target, prior, change)


def rate_moves(history: RateHistory) -> RateMoves:
    """Finds the observations at which each series' rate changes.

    The first observation of a series is not a move.

    Args:
        history: The fixed-point arrays.

    Returns:
        The index and size of every move.
    """
    values, starts = history.values, history.offsets[:-1]
    moved = np.zeros(len(values), dtype=np.bool_)
    moved[1:] = values[1:] != values[:-1]
    # A series' first observation was compared with the last one of the series
    # before; a series with no rows starts at len(values), past the end.
    moved[starts[starts < len(values)]] = False
    index = np.flatnonzero(moved)
    return RateMoves(index, values[index] - values[index - 1])


# --- The searches -------------------------------------------------
# Each one has a NumPy version and a loop that numba can compile.


def _prior_index_numpy(
    series_days: NDArray[np.int64],
    offsets: NDArray[np.int64],
    targets: NDArray[np.int32],
) -> NDArray[np.int64]:
    """For each observation, the last one of its series on or before its target."""
    series = np.repeat(np.arange(len(offsets) - 1), np.diff(offsets))
    wanted = series * SERIES_STEP + targets
    found = np.searchsorted(series_days, wanted, side="right") - 1
    # A hit before the series' first row belongs to the series before: none found.
    return np.where(found >= offsets[series], found, NO_INDEX)


def _prior_index_loop(
    series_days: NDArray[np.int64],
    offsets: NDArray[np.int64],
    targets: NDArray[np.int32],
) -> NDArray[np.int64]:
    prior = np.full(len(series_days), NO_INDEX, dtype=np.int64)
    for series in range(len(offsets) - 1):
        start, end = offsets[series], offsets[series + 1]
        found = start - 1
        for i in range(start, end):
            # Targets rise with the dates, so each search starts from the last one.
            # np.int64: a Python int plus an int32 item stays int32 and overflows.
            wanted = series * SERIES_STEP + np.int64(targets[i])
            while found + 1 < end and series_days[found + 1] <= wanted:
                found += 1
            if found >= start:
                prior[i] = found
    return prior


def _last_on_or_before_numpy(
    series_days: NDArray[np.int64],
    offsets: NDArray[np.int64],
    limits: NDArray[np.int32],
) -> NDArray[np.int64]:
    """For each series, its last observation on or before the series' limit."""
    series = np.arange(len(offsets) - 1)
    wanted = series * SERIES_STEP + limits
    found = np.searchsorted(series_days, wanted, side="right") - 1
    return np.where(found >= offsets[:-1], found, NO_INDEX)


def _last_on_or_before_loop(
    series_days: NDArray[np.int64],
    offsets: NDArray[np.int64],
    limits: NDArray[np.int32],
) -> NDArray[np.int64]:
    found = np.full(len(offsets) - 1, NO_INDEX, dtype=np.int64)
    for series in range(len(offsets) - 1):
        wanted = series * SERIES_STEP + np.int64(limits[series])
        low, high = offsets[series], offsets[series + 1]
        start = low
        while low < high:  # binary search for the first row after the limit
            middle = (low + high) // 2
            if series_days[middle] <= wanted:
                low = middle + 1
            else:
                high = middle
        if low > start:
            found[series] = low - 1
    return found


if njit is None:
    _prior_index = _prior_index_numpy
    _last_on_or_before = _last_on_or_before_numpy
else:
    # cache: the compiled code is kept on disk, so only the first process compiles.
    _prior_index = njit(cache=True)(_prior_index_loop)
    _last_on_or_before = njit(cache=True)(_last_on_or_before_loop)
