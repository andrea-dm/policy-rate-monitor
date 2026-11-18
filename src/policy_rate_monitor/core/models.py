"""Domain models exchanged between the layers.

Providers produce ``SeriesMetadata``, ``DataQualityIssue`` and ``SourceRelease``;
analytics produces ``DataSnapshot``; reporting and the API serialise them. All models
are frozen pydantic models whose invariants are checked when they are built, so an
inconsistent object cannot exist.

Rates are ``Decimal`` end to end. In JSON they are written as numbers, not as strings
(pydantic's default for ``Decimal``), so ``summary.json`` and the API stay numeric.
"""

from __future__ import annotations

# pydantic resolves the annotations at runtime, so ``date`` is a runtime import.
from datetime import date  # noqa: TC003
from decimal import Decimal
from enum import StrEnum, auto
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

__all__ = [
    "BasisPoints",
    "DataQualityIssue",
    "DataQualitySeverity",
    "DataSnapshot",
    "Rate",
    "SeriesMetadata",
    "SnapshotFlag",
    "SnapshotStatus",
    "SourceRelease",
]

_BASIS_POINTS_PER_PERCENT = Decimal(100)


def _decimal_to_json_number(value: Decimal) -> float:
    # A float is what a JSON number is. ``float`` gives the shortest repr, so 25.0 stays
    # 25.0 and 0.1 stays 0.1: the calculation itself never touches a float.
    return float(value)


_JsonNumber = PlainSerializer(
    _decimal_to_json_number, return_type=float, when_used="json"
)

Rate = Annotated[Decimal, Field(allow_inf_nan=False), _JsonNumber]
"""A policy rate in per cent, exact. Serialised to JSON as a number."""

BasisPoints = Annotated[Decimal, Field(allow_inf_nan=False), _JsonNumber]
"""A rate difference in basis points (1 percentage point = 100 bps), exact."""

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SeriesMetadata(_FrozenModel):
    """Descriptive attributes of one time series, as published by the source.

    Attributes:
        series_key: The source's key of the series, e.g. ``"D.CH"``.
        ref_area: Code of the country or economic area, e.g. ``"CH"``.
        ref_area_name: Its name, e.g. ``"Switzerland"``.
        frequency: Frequency code, e.g. ``"D"`` for daily.
        title: The series title.
        unit: Unit of measure, e.g. ``"Per cent per year"``; ``None`` if not published.
        decimals: Display precision published by the source. Used for formatting,
            never for rounding stored values.
        attributes: Any other source attribute, by name, kept as published.
    """

    series_key: NonEmptyStr
    ref_area: NonEmptyStr
    ref_area_name: NonEmptyStr
    frequency: NonEmptyStr
    title: NonEmptyStr
    unit: str | None = None
    decimals: Annotated[int, Field(ge=0)] | None = None
    attributes: dict[str, str] = Field(default_factory=dict[str, str])


class SnapshotStatus(StrEnum):
    """Outcome of the snapshot calculation for one requested country or area."""

    OK = auto()
    """Latest rate, prior rate and change are all available."""
    UNKNOWN_AREA = auto()
    """No series exists for the requested code."""
    NO_VALID_DATA = auto()
    """The series exists but has no valid observation."""
    INSUFFICIENT_HISTORY = auto()
    """A latest rate exists, but no valid observation on or before the target date."""


class SnapshotFlag(StrEnum):
    """Warnings about a snapshot. A flag never suppresses a value."""

    STALE_LATEST = auto()
    """The latest valid observation is much older than the dataset's latest date."""
    STALE_PRIOR = auto()
    """The prior observation is much older than the target date (a gap in the data)."""


class DataSnapshot(_FrozenModel):
    """Latest policy rate, the rate 12 months earlier, and the change, for one request.

    The look-back is relative to the series' own latest valid date: ``target_date`` is
    ``latest_date`` minus the configured number of months, and the prior observation is
    the last valid one on or before ``target_date``.

    Attributes:
        requested_ref_area: The code as requested, e.g. ``"ea"`` (whitespace stripped).
        ref_area: The normalised code after aliases, e.g. ``"XM"``.
        series: Metadata of the matched series; ``None`` only for ``UNKNOWN_AREA``.
        latest_date: Date of the latest valid observation.
        latest_value: Its rate, in per cent.
        target_date: ``latest_date`` minus the look-back.
        prior_date: Date of the last valid observation on or before ``target_date``.
        prior_value: Its rate, in per cent.
        change_bps: ``(latest_value - prior_value) * 100``, exactly.
        status: The outcome; see ``SnapshotStatus``.
        flags: Warnings, sorted and without duplicates.
    """

    requested_ref_area: str
    ref_area: NonEmptyStr
    series: SeriesMetadata | None = None
    latest_date: date | None = None
    latest_value: Rate | None = None
    target_date: date | None = None
    prior_date: date | None = None
    prior_value: Rate | None = None
    change_bps: BasisPoints | None = None
    status: SnapshotStatus
    flags: tuple[SnapshotFlag, ...] = ()

    @field_validator("flags")
    @classmethod
    def _sorted_unique_flags(
        cls, flags: tuple[SnapshotFlag, ...]
    ) -> tuple[SnapshotFlag, ...]:
        # A tuple, not a set: set order depends on string hashing, which varies between
        # runs, and the outputs must be byte-for-byte reproducible.
        return tuple(sorted(set(flags)))

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.status is SnapshotStatus.UNKNOWN_AREA:
            self._require_absent("series", *self._value_fields())
            if self.flags:
                msg = "an UNKNOWN_AREA snapshot carries no flags"
                raise ValueError(msg)
            return self
        if self.series is None:
            msg = f"a {self.status} snapshot needs its series metadata"
            raise ValueError(msg)
        if self.series.ref_area != self.ref_area:
            msg = (
                f"ref_area {self.ref_area!r} differs from the series' "
                f"{self.series.ref_area!r}"
            )
            raise ValueError(msg)
        match self.status:
            case SnapshotStatus.NO_VALID_DATA:
                self._require_absent(*self._value_fields())
            case SnapshotStatus.INSUFFICIENT_HISTORY:
                self._require_present("latest_date", "latest_value", "target_date")
                self._require_absent("prior_date", "prior_value", "change_bps")
            case SnapshotStatus.OK:
                self._require_present(*self._value_fields())
                self._check_ok_values()
        self._check_flags()
        return self

    @staticmethod
    def _value_fields() -> tuple[str, ...]:
        return (
            "latest_date",
            "latest_value",
            "target_date",
            "prior_date",
            "prior_value",
            "change_bps",
        )

    def _require_present(self, *names: str) -> None:
        if missing := [name for name in names if getattr(self, name) is None]:
            msg = f"a {self.status} snapshot needs {', '.join(missing)}"
            raise ValueError(msg)

    def _require_absent(self, *names: str) -> None:
        if present := [name for name in names if getattr(self, name) is not None]:
            msg = f"a {self.status} snapshot cannot have {', '.join(present)}"
            raise ValueError(msg)

    def _present[T](self, name: str, value: T | None) -> T:
        # Returns the value with ``None`` excluded, which also narrows its type.
        if value is None:
            msg = f"a {self.status} snapshot needs {name}"
            raise ValueError(msg)
        return value

    def _check_ok_values(self) -> None:
        prior_date = self._present("prior_date", self.prior_date)
        target_date = self._present("target_date", self.target_date)
        latest_date = self._present("latest_date", self.latest_date)
        if not prior_date <= target_date <= latest_date:
            msg = (
                "dates must satisfy prior_date <= target_date <= latest_date, got "
                f"{prior_date}, {target_date}, {latest_date}"
            )
            raise ValueError(msg)
        latest = self._present("latest_value", self.latest_value)
        prior = self._present("prior_value", self.prior_value)
        expected = (latest - prior) * _BASIS_POINTS_PER_PERCENT
        if self.change_bps != expected:
            msg = f"change_bps {self.change_bps} != (latest - prior) * 100 = {expected}"
            raise ValueError(msg)

    def _check_flags(self) -> None:
        if SnapshotFlag.STALE_LATEST in self.flags and self.latest_date is None:
            msg = "STALE_LATEST needs a latest observation"
            raise ValueError(msg)
        if SnapshotFlag.STALE_PRIOR in self.flags and self.prior_date is None:
            msg = "STALE_PRIOR needs a prior observation"
            raise ValueError(msg)


class DataQualitySeverity(StrEnum):
    """How serious a data-quality finding is."""

    INFO = auto()
    """Expected and handled, e.g. exact duplicate rows dropped."""
    WARNING = auto()
    """Handled, but worth a look, e.g. a non-numeric value treated as missing."""
    ERROR = auto()
    """The data cannot be used as it is, e.g. a required column is missing."""


class DataQualityIssue(_FrozenModel):
    """One finding of a data-quality rule.

    Attributes:
        rule: Name of the rule as registered and configured, e.g. ``"excess_decimals"``.
        severity: How serious the finding is.
        series_key: The affected series; ``None`` for a finding about the whole dataset.
        detail: Human-readable explanation, including what was done about it.
        count: Number of affected observations (0 when the rule could not be evaluated).
    """

    rule: NonEmptyStr
    severity: DataQualitySeverity
    series_key: NonEmptyStr | None = None
    detail: NonEmptyStr
    count: Annotated[int, Field(ge=0)] = 1


class SourceRelease(_FrozenModel):
    """One version of the source data: where it came from, which one, and when.

    Attributes:
        location: Where the data was read from: a URL or a file path.
        version_id: The source's own version identifier, if it has one (for the bulk
            download, the HTTP ETag).
        checksum_sha256: SHA-256 of the retrieved content, as 64 hex digits.
        retrieved_at: When the content was retrieved, timezone-aware.
    """

    location: NonEmptyStr
    version_id: str | None = None
    checksum_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    retrieved_at: AwareDatetime
