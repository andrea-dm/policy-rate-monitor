"""Domain models exchanged between the layers.

``fetch`` produces ``SourceRelease``; ``transform`` produces ``SeriesMetadata`` and
``DataQualityIssue`` and returns a ``TransformSummary``; ``snapshot`` produces
``DataSnapshot``; providers serve them, and the report serialises them. All models
are frozen pydantic models whose invariants are checked whenever they are built
through the constructor or ``model_validate``/``model_validate_json``. pydantic's
``model_copy(update=...)`` and ``model_construct`` skip validation by design: derive
a changed object with ``Model.model_validate({**obj.model_dump(), "field": value})``.

Rates are ``Decimal`` end to end. In JSON they are written as numbers, not as strings
(pydantic's default for ``Decimal``), so ``summary.json`` and the API stay numeric. A
JSON number is a double, so a rate is limited to 15 significant digits, which a double
carries exactly: the JSON then reads back as an equal object.

See also: [Glossary](glossary.md) and [decision 0004](adr/0004-domain-model.md).
"""

from __future__ import annotations

# pydantic resolves the annotations at runtime, so ``date`` is a runtime import.
from collections.abc import Iterator, Mapping
from datetime import date  # noqa: TC003
from decimal import Decimal
from enum import StrEnum, auto
from typing import Annotated, Self, override

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

__all__ = [
    "BASIS_POINTS_PER_PERCENT",
    "BasisPoints",
    "DataQualityIssue",
    "DataQualitySeverity",
    "DataSnapshot",
    "Rate",
    "ReadOnlyAttributes",
    "SeriesMetadata",
    "SnapshotFlag",
    "SnapshotStatus",
    "SourceRelease",
    "TransformSummary",
]

BASIS_POINTS_PER_PERCENT = Decimal(100)
"""Basis points in one percentage point; a rate change times this is ``BasisPoints``."""


def _decimal_to_json_number(value: Decimal) -> float:
    # A float is what a JSON number is. ``float`` gives the shortest repr, so 25.0 stays
    # 25.0 and 0.1 stays 0.1: the calculation itself never touches a float.
    return float(value)


_JsonNumber = PlainSerializer(
    _decimal_to_json_number, return_type=float, when_used="json"
)

# strict: Python callers must pass a Decimal. A float would carry its binary rounding
# into a value the model then treats as exact. JSON text (model_validate_json) still
# accepts JSON numbers; already-parsed JSON (model_validate(response.json())) holds
# floats and is rejected, so read API responses with model_validate_json. Revisit
# (a context-gated BeforeValidator on this type) if a request body ever carries rates.
# max_digits: what a JSON double carries exactly (module docstring); it also rejects
# values outside the double range, which JSON would write as null.
_EXACT_DECIMAL = Field(strict=True, allow_inf_nan=False, max_digits=15)

Rate = Annotated[Decimal, _EXACT_DECIMAL, _JsonNumber]
"""A policy rate in per cent, exact. Serialised to JSON as a number."""

BasisPoints = Annotated[Decimal, _EXACT_DECIMAL, _JsonNumber]
"""A rate difference in basis points (1 percentage point = 100 bps), exact."""

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ReadOnlyAttributes(Mapping[str, str]):
    """An immutable, hashable mapping of source attributes, sorted by name.

    A frozen model's ``dict`` field could still be changed in place, and it makes the
    model unhashable. This mapping cannot be changed, hashes by its items, and copies
    and pickles like any value.

    Examples:
        >>> attributes = ReadOnlyAttributes({"UNIT_MULT": "0", "DECIMALS": "4"})
        >>> list(attributes)
        ['DECIMALS', 'UNIT_MULT']
        >>> attributes["DECIMALS"] = "2"
        Traceback (most recent call last):
            ...
        TypeError: 'ReadOnlyAttributes' object does not support item assignment
    """

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        """Copies *items*, sorted by key."""
        super().__init__()
        self._items: dict[str, str] = dict(sorted((items or {}).items()))

    @override
    def __getitem__(self, key: str) -> str:
        """Returns the attribute value for *key*."""
        return self._items[key]

    @override
    def __iter__(self) -> Iterator[str]:
        """Iterates over the attribute names, in sorted order."""
        return iter(self._items)

    @override
    def __len__(self) -> int:
        """Returns the number of attributes."""
        return len(self._items)

    @override
    def __hash__(self) -> int:
        """Hashes by the items, so equal mappings hash equal."""
        return hash(tuple(self._items.items()))

    @override
    def __repr__(self) -> str:
        """Shows the items, e.g. ``ReadOnlyAttributes({'A': '1'})``."""
        return f"{type(self).__name__}({self._items!r})"


def _read_only(value: Mapping[str, str]) -> ReadOnlyAttributes:
    return value if isinstance(value, ReadOnlyAttributes) else ReadOnlyAttributes(value)


def _plain_dict(value: Mapping[str, str]) -> dict[str, str]:
    return dict(value)


Attributes = Annotated[
    Mapping[str, str],
    AfterValidator(_read_only),
    PlainSerializer(_plain_dict, return_type=dict[str, str]),
]
"""Source attributes by name: read-only, sorted by name, serialised as an object."""

_NO_ATTRIBUTES = ReadOnlyAttributes()


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
    unit: NonEmptyStr | None = None
    decimals: Annotated[int, Field(ge=0)] | None = None
    attributes: Attributes = _NO_ATTRIBUTES


class SnapshotStatus(StrEnum):
    """Outcome of the snapshot calculation for one requested country or area."""

    OK = auto()
    """Latest rate, prior rate and change are all available."""
    UNKNOWN_REF_AREA = auto()
    """No series exists for the requested code."""
    NO_VALID_DATA = auto()
    """The series exists but has no valid observation."""
    INSUFFICIENT_HISTORY = auto()
    """A latest rate exists, but no valid observation on or before the target date."""


class SnapshotFlag(StrEnum):
    """Warnings about a snapshot. A flag never suppresses a value."""

    STALE_LATEST = auto()
    """The latest valid observation is more than ``stale_latest_days`` (in the
    configuration) before the newest date in the data."""
    STALE_PRIOR = auto()
    """The prior observation is more than ``stale_prior_days`` (in the configuration)
    before the target date: a gap in the data."""


class DataSnapshot(_FrozenModel):
    """Latest policy rate, the rate 12 months earlier, and the change, for one request.

    The look-back is relative to the series' own latest valid date: ``target_date`` is
    ``latest_date`` minus the configured number of months, and the prior observation is
    the last valid one on or before ``target_date``.

    Attributes:
        requested_ref_area: The code as requested, e.g. ``"ea"`` (whitespace stripped).
        ref_area: The normalised code after aliases, e.g. ``"XM"``.
        series: Metadata of the matched series; ``None`` only for ``UNKNOWN_REF_AREA``.
        latest_date: Date of the latest valid observation.
        latest_value: Its rate, in per cent.
        target_date: ``latest_date`` minus the look-back.
        prior_date: Date of the last valid observation on or before ``target_date``.
        prior_value: Its rate, in per cent.
        change_bps: ``(latest_value - prior_value) * 100``, exactly.
        status: The outcome; see ``SnapshotStatus``.
        flags: Warnings, sorted and without duplicates.

    Examples:
        A 25 bp hike, exact (in floats, ``(4.10 - 3.85) * 100`` is 24.999999999999957):

        >>> from datetime import date
        >>> from decimal import Decimal
        >>> series = SeriesMetadata(
        ...     series_key="D.CH",
        ...     ref_area="CH",
        ...     ref_area_name="Switzerland",
        ...     frequency="D",
        ...     title="Policy rate, Switzerland, daily",
        ... )
        >>> snapshot = DataSnapshot(
        ...     requested_ref_area="ch",
        ...     ref_area="CH",
        ...     series=series,
        ...     latest_date=date(2026, 11, 10),
        ...     latest_value=Decimal("4.10"),
        ...     target_date=date(2025, 11, 10),
        ...     prior_date=date(2025, 11, 7),
        ...     prior_value=Decimal("3.85"),
        ...     change_bps=Decimal("25.00"),
        ...     status=SnapshotStatus.OK,
        ... )
        >>> snapshot.change_bps
        Decimal('25.00')
        >>> snapshot.model_dump(mode="json")["change_bps"]
        25.0

        A change that does not match the rates is rejected when the object is built:

        >>> from pydantic import ValidationError
        >>> try:
        ...     DataSnapshot.model_validate(
        ...         {**snapshot.model_dump(), "change_bps": Decimal("24.99")}
        ...     )
        ... except ValidationError as error:
        ...     print(error.errors()[0]["msg"])
        Value error, change_bps 24.99 != (latest - prior) * 100 = 25.00
    """

    requested_ref_area: NonEmptyStr
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
        if self.status is SnapshotStatus.UNKNOWN_REF_AREA:
            self._require_absent("series", *self._value_fields())
            if self.flags:
                msg = "an UNKNOWN_REF_AREA snapshot carries no flags"
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
                self._check_dates()
            case SnapshotStatus.OK:
                self._require_present(*self._value_fields())
                self._check_dates()
                self._check_change()
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

    def _check_dates(self) -> None:
        # prior_date <= target_date <= latest_date, for whichever of them are present.
        present = [
            d for d in (self.prior_date, self.target_date, self.latest_date) if d
        ]
        if present != sorted(present):
            msg = (
                "dates must satisfy prior_date <= target_date <= latest_date, got "
                f"{self.prior_date}, {self.target_date}, {self.latest_date}"
            )
            raise ValueError(msg)

    def _check_change(self) -> None:
        latest = self._present("latest_value", self.latest_value)
        prior = self._present("prior_value", self.prior_value)
        expected = (latest - prior) * BASIS_POINTS_PER_PERCENT
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
        rule: Name of the rule, as configured, e.g. ``"exact_duplicate"``.
        severity: How serious the finding is.
        series_key: The affected series; ``None`` for a finding about the whole dataset.
        detail: Human-readable explanation, including what was done about it.
        count: Number of affected observations. At least 1 today; 0 is kept for a
            rule that could not be evaluated, which no rule reports yet.
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
        version_id: The source's own version identifier, if it has one. The bulk
            download has none, so it is ``None``.
        checksum_sha256: SHA-256 of the retrieved content, as 64 hex digits.
        retrieved_at: When the content was retrieved, timezone-aware.
    """

    location: NonEmptyStr
    version_id: NonEmptyStr | None = None
    checksum_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    retrieved_at: AwareDatetime


class TransformSummary(_FrozenModel):
    """What one transform run produced: returned by ``DataProvider.transform``, and
    the same counts are written to the log.

    Attributes:
        rows_read: Rows in the source data.
        observations: Observations staged.
        series: Series staged.
        issues: Data-quality findings staged.
    """

    rows_read: Annotated[int, Field(ge=0)]
    observations: Annotated[int, Field(ge=0)]
    series: Annotated[int, Field(ge=0)]
    issues: Annotated[int, Field(ge=0)]
