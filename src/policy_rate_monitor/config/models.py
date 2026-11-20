"""The configuration of one dataset, as frozen and validated pydantic models.

``DatasetConfig`` is the top-level model, with one section per concern: the dataset's
identity, where its file comes from, its SDMX structure, how that structure is written
in the file, the data-quality rules, the snapshot thresholds, the output fields and
the area aliases. Nothing here names a real dataset: every fact comes from the
configuration document.

Every model is frozen, forbids unknown keys and is strict: ``"24"`` is not an integer
and ``"false"`` is not a boolean. Strict mode reads JSON text, so build a configuration
with ``parse_config`` (or ``model_validate_json``), not from a parsed ``dict``. A check
that needs one field runs on its type, so it reports even while another section fails.
Checks across fields run once those fields are valid.

See also: [Configuration](configuration.md) and [decision
0007](adr/0007-config-driven-design.md).

Examples:
    >>> CANONICAL_COLUMNS
    ('series_key', 'date', 'value', 'is_valid')
    >>> measure = Measure.model_validate_json(
    ...     '{"id": "VAL", "precision": 15, "scale": 6, "unit_multiplier": null}'
    ... )
    >>> measure.precision, measure.scale
    (15, 6)
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from io import BytesIO, TextIOWrapper
from pathlib import Path  # noqa: TC003  (pydantic resolves the annotation at runtime)
from typing import Annotated, Final, Literal, Self, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    PlainSerializer,
    model_validator,
)

from policy_rate_monitor.config._problems import (
    ALIAS_CHAIN,
    ALIAS_NOT_UPPER_CASE,
    ALIAS_TO_ITSELF,
    COLUMN_RESERVED,
    COLUMN_TWICE,
    CONCEPT_CASE_CLASH,
    CONCEPT_TWICE,
    DATE_FORMAT_NO_ROUND_TRIP,
    FALLBACK_URL_NAME,
    FREQUENCY_IS_REF_AREA,
    INVALID_CODES_NOT_CODED,
    LISTED_TWICE,
    NOT_A_CODED_SERIES_ATTRIBUTE,
    NOT_A_DIMENSION,
    NOT_A_SERIES_ATTRIBUTE,
    OPTIONAL_IS_NEEDED,
    OPTIONAL_UNDECLARED,
    OUTPUT_FIELD_UNBOUND,
    PATH_DOLLAR,
    PATH_EMPTY,
    PATH_TILDE,
    REF_AREA_NOT_CODED,
    RULE_TWICE,
    SCALE_ABOVE_PRECISION,
    SELECTION_MISSING,
    SELECTION_NOT_SELECTABLE,
    SELECTION_REF_AREA,
    UNKNOWN_ENCODING,
    UNMAPPED_CASE_CLASH,
    UNMAPPED_IS_DECLARED,
    Loc,
    Problem,
    closest,
    did_you_mean,
    raise_problems,
)
from policy_rate_monitor.core.models import DataQualitySeverity, ReadOnlyAttributes

__all__ = [
    "CANONICAL_COLUMNS",
    "ColumnsSection",
    "Concept",
    "DatasetConfig",
    "DatasetSection",
    "Dimension",
    "Loc",
    "Measure",
    "MetadataConcepts",
    "MetadataField",
    "ObservationAttribute",
    "OutputSection",
    "Role",
    "RuleSpec",
    "SnapshotSection",
    "SourceSection",
    "StructureSection",
    "TimeDimension",
]

CANONICAL_COLUMNS: Final = ("series_key", "date", "value", "is_valid")
"""The fixed columns of the canonical observations table, in table order."""

MetadataField = Literal[
    "series_key", "ref_area", "ref_area_name", "frequency", "title", "unit", "decimals"
]
"""A ``SeriesMetadata`` field that an output row can carry (all but ``attributes``)."""

Role = Literal[
    "dimension", "time", "measure", "observation_attribute", "series_attribute"
]
"""The role of a declared concept in ``structure``."""

_SNAKE_CASE = r"^[a-z][a-z0-9_]*$"
_T = TypeVar("_T")

ConceptId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
Code = Annotated[str, Field(pattern=r"^[A-Za-z0-9_@$-]+$")]
ColumnName = Annotated[str, Field(pattern=_SNAKE_CASE)]
RuleName = Annotated[str, Field(pattern=_SNAKE_CASE)]
DatasetId = Annotated[str, Field(pattern=_SNAKE_CASE)]
Text = Annotated[str, Field(min_length=1)]
FileName = Annotated[str, Field(pattern=r"^[^/\\]+$")]
PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]

# Two dates whose day, month and year all differ, so a pattern that loses or swaps
# one of them cannot give both back. 1950 lies outside strptime's two-digit-year
# window (1969-2068), so "%y" fails instead of reading 1950 as 2050.
_ROUND_TRIP_DATES: Final = (date(1950, 2, 3), date(2004, 11, 30))


def _check_local_path(value: object) -> object:
    # Before validation: ``Path("")`` would already be ``.``.
    if isinstance(value, str):
        if not value:
            raise PATH_EMPTY.error()
        # TODO(andrea-dm): also reject a NUL character and a path of spaces only.
        # https://github.com/andrea-dm/policy-rate-monitor/issues/8
        if value.startswith("~"):
            raise PATH_TILDE.error(path=value)
        if "$" in value:
            raise PATH_DOLLAR.error(path=value)
    return value


def _check_date_format(pattern: str) -> str:
    for day in _ROUND_TRIP_DATES:
        try:
            # Python 3.13+ warns when a pattern has a day but no year. Such a pattern
            # fails the round trip anyway; under -W error the warning would escape
            # as an exception instead of this validation error.
            # TODO(andrea-dm): catch_warnings is not thread-safe; catch the warning.
            # https://github.com/andrea-dm/policy-rate-monitor/issues/8
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                parsed = datetime.strptime(day.strftime(pattern), pattern).replace(
                    tzinfo=UTC
                )
        except (ValueError, re.error):
            # A repeated directive ("%Y%Y") makes strptime's own regex fail with
            # re.error, which is not a ValueError.
            raise DATE_FORMAT_NO_ROUND_TRIP.error(format=pattern) from None
        if parsed.date() != day:
            raise DATE_FORMAT_NO_ROUND_TRIP.error(format=pattern)
    return pattern


def _check_encoding(name: str) -> str:
    try:
        # The check open() makes: an unknown name and a codec that is not a text
        # encoding (rot13, base64) raise LookupError; a NUL raises ValueError.
        TextIOWrapper(BytesIO(), encoding=name)
    except (LookupError, ValueError):
        raise UNKNOWN_ENCODING.error(encoding=name) from None
    return name


def _unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    first: dict[T, int] = {}
    problems: list[Problem] = []
    for index, value in enumerate(values):
        if value in first:
            # TODO(andrea-dm): put "value" last: fields are filled in this order.
            # https://github.com/andrea-dm/policy-rate-monitor/issues/8
            context = {"value": str(value), "first": first[value]}
            problems.append(Problem(LISTED_TWICE, (index,), value, context))
        else:
            first[value] = index
    raise_problems("list", problems)
    return values


def _read_only(value: Mapping[str, str]) -> ReadOnlyAttributes:
    return ReadOnlyAttributes(value)


def _plain_dict(value: Mapping[str, str]) -> dict[str, str]:
    return dict(value)


def _check_aliases(aliases: Mapping[str, str]) -> Mapping[str, str]:
    problems: list[Problem] = []
    for code, target in aliases.items():
        if code != code.upper():
            problems.append(
                Problem(ALIAS_NOT_UPPER_CASE, (code,), code, {"code": code})
            )
        elif code == target:
            problems.append(Problem(ALIAS_TO_ITSELF, (code,), target, {"code": code}))
        elif target in aliases:
            context = {"code": code, "target": target}
            problems.append(Problem(ALIAS_CHAIN, (code,), target, context))
    raise_problems("area_aliases", problems)
    return aliases


LocalPath = Annotated[Path, BeforeValidator(_check_local_path)]
UniqueTuple = Annotated[tuple[_T, ...], AfterValidator(_unique)]
_AsDict = PlainSerializer(_plain_dict, return_type=dict[str, str])
Selection = Annotated[Mapping[ConceptId, Code], AfterValidator(_read_only), _AsDict]
AreaAliases = Annotated[
    Mapping[Code, Code],
    AfterValidator(_check_aliases),
    AfterValidator(_read_only),
    _AsDict,
]


# TODO(andrea-dm): one "first at" format for every repeat message (not the parser's).
# https://github.com/andrea-dm/policy-rate-monitor/issues/8
def _where(loc: Loc) -> str:
    """Renders a location inside ``structure`` the way the parser does."""
    text = "structure"
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else f".{part}"
    return text


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        use_attribute_docstrings=True,
    )


class DatasetSection(_ConfigModel):
    """Identity and defaults of the dataset."""

    id: DatasetId
    """Identifier in snake_case, used in provenance and log lines."""
    # TODO(andrea-dm): reject a title, description or attribution of whitespace only.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    title: Text
    """Heading of the report."""
    description: Text
    """What the data is: the report's "About the data" paragraph."""
    attribution: Text
    """Source credit, shown in the report's "About the data" section."""
    data_dir: LocalPath
    """Default data directory. A relative path is resolved against the working
    directory when the command runs; '~' and '$' are not expanded."""


class SourceSection(_ConfigModel):
    """Where the source file is found and how it is downloaded."""

    discovery_url: HttpUrl
    """Page whose links are searched for file_name."""
    file_name: FileName
    """Exact last path segment of the link to pick; also the cached file's name."""
    fallback_url: HttpUrl
    """Download URL used when the page is unreachable or has no matching link. It
    must end in file_name."""
    cache_ttl_hours: Annotated[int, Field(ge=0)]
    """How long a cached copy counts as fresh, in hours. 0 always revalidates."""
    max_attempts: Annotated[int, Field(ge=1)]
    """HTTP attempts per download, the first one included."""
    connect_timeout_seconds: PositiveFloat
    """Time allowed to open a connection, in seconds."""
    read_timeout_seconds: PositiveFloat
    """Time allowed between two received chunks, in seconds."""

    @model_validator(mode="after")
    def _check_fallback_name(self) -> Self:
        # TODO(andrea-dm): compare file_name with the decoded segment.
        # https://github.com/andrea-dm/policy-rate-monitor/issues/8
        last = (self.fallback_url.path or "").rsplit("/", 1)[-1]
        if last != self.file_name:
            context = {"last": last, "file_name": self.file_name}
            problem = Problem(
                FALLBACK_URL_NAME, ("fallback_url",), str(self.fallback_url), context
            )
            raise_problems(type(self).__name__, [problem])
        return self


class Concept(_ConfigModel):
    """An SDMX concept: a column of the source file."""

    id: ConceptId
    """Concept id, the code part of the column header (the part before the header
    label separator)."""
    coded: bool
    """True if values are written as code and label, split at the first value label
    separator; false if the value is taken whole (free text)."""


class Dimension(Concept):
    """A dimension of the series key."""

    column: ColumnName
    """Name of its column in the canonical observations table."""


class TimeDimension(_ConfigModel):
    """The concept that holds the observation date."""

    id: ConceptId
    """Concept id of the time period."""
    date_format: Annotated[str, AfterValidator(_check_date_format)]
    """strptime pattern of the dates, e.g. '%Y-%m-%d'. It must give back the year,
    month and day."""


class Measure(_ConfigModel):
    """The concept that holds the observation value."""

    id: ConceptId
    """Concept id of the value."""
    # TODO(andrea-dm): take the 15-digit cap from a public constant in core.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    precision: Annotated[int, Field(ge=1, le=15)]
    """Total significant digits of the stored decimal value (at most 15, what an
    exact rate allows)."""
    scale: Annotated[int, Field(ge=0)]
    """Digits after the decimal point of the stored value (at most precision)."""
    unit_multiplier: ConceptId | None
    """Coded series attribute whose code is the power of ten the published value is
    multiplied by; null if values are stored as published."""

    @model_validator(mode="after")
    def _check_scale(self) -> Self:
        if self.scale > self.precision:
            context = {"scale": self.scale, "precision": self.precision}
            problem = Problem(SCALE_ABOVE_PRECISION, ("scale",), self.scale, context)
            raise_problems(type(self).__name__, [problem])
        return self


class ObservationAttribute(Concept):
    """An attribute that can change from one observation to the next."""

    # ELT: the raw layer keeps every attribute as loaded. ``keep_as`` only decides
    # whether staging also carries it into the canonical table, so null loses no data.
    keep_as: ColumnName | None
    """Name of its column in the canonical observations table; null if it stays in
    the raw data only, read for checks."""
    invalid_codes: UniqueTuple[Code] = ()
    """Codes that make an observation invalid whatever its value."""

    @model_validator(mode="after")
    def _check_invalid_codes(self) -> Self:
        if self.invalid_codes and not self.coded:
            problem = Problem(
                INVALID_CODES_NOT_CODED,
                ("invalid_codes",),
                list(self.invalid_codes),
                {"id": self.id},
            )
            raise_problems(type(self).__name__, [problem])
        return self


class MetadataConcepts(_ConfigModel):
    """Which concept fills each SeriesMetadata field."""

    ref_area: ConceptId
    """Coded dimension whose code is ref_area and whose label is ref_area_name."""
    frequency: ConceptId
    """Dimension whose code is the frequency."""
    title: ConceptId
    """Series attribute that holds the title."""
    unit: ConceptId | None
    """Series attribute that holds the unit of measure; null if there is none."""
    decimals: ConceptId | None
    """Series attribute that holds the display decimals; null if there is none."""


class StructureSection(_ConfigModel):
    """The source's concepts, in SDMX terms, and the series to keep."""

    dimensions: Annotated[tuple[Dimension, ...], Field(min_length=1)]
    """The dimensions of the series key, in key order."""
    time_dimension: TimeDimension
    """The time period."""
    measure: Measure
    """The observation value."""
    observation_attributes: tuple[ObservationAttribute, ...]
    """Attributes that vary per observation."""
    series_attributes: tuple[Concept, ...]
    """Attributes constant within a series; they become series metadata."""
    series_metadata: MetadataConcepts
    """The concept bound to each SeriesMetadata field."""
    selection: Selection
    """One code for each dimension other than the reference area, matched on the
    code, e.g. a frequency dimension fixed to its daily code."""

    def concepts(self) -> Iterator[tuple[Loc, str, Role]]:
        """Yields every declared concept with its location and role.

        Yields:
            The location inside ``structure``, the concept id and its role, in
            declaration order: dimensions, time, measure, observation attributes,
            series attributes.
        """
        for index, dimension in enumerate(self.dimensions):
            yield ("dimensions", index), dimension.id, "dimension"
        yield ("time_dimension",), self.time_dimension.id, "time"
        yield ("measure",), self.measure.id, "measure"
        for index, attribute in enumerate(self.observation_attributes):
            yield (
                ("observation_attributes", index),
                attribute.id,
                "observation_attribute",
            )
        for index, concept in enumerate(self.series_attributes):
            yield ("series_attributes", index), concept.id, "series_attribute"

    @model_validator(mode="after")
    def _check_structure(self) -> Self:
        problems = [
            *self._duplicate_concepts(),
            *self._column_clashes(),
            *self._selection_problems(),
            *self._metadata_problems(),
            *self._unit_multiplier_problems(),
        ]
        raise_problems(type(self).__name__, problems)
        return self

    def _duplicate_concepts(self) -> list[Problem]:
        seen: dict[str, tuple[Loc, str]] = {}
        problems: list[Problem] = []
        for loc, concept_id, _role in self.concepts():
            key = concept_id.upper()
            if key not in seen:
                seen[key] = (loc, concept_id)
                continue
            first_loc, first_id = seen[key]
            context = {
                "id": concept_id,
                "first": _where(first_loc),
                "first_id": first_id,
            }
            template = CONCEPT_TWICE if first_id == concept_id else CONCEPT_CASE_CLASH
            problems.append(Problem(template, (*loc, "id"), concept_id, context))
        return problems

    def _column_clashes(self) -> list[Problem]:
        named: list[tuple[Loc, str]] = [
            (("dimensions", index, "column"), dimension.column)
            for index, dimension in enumerate(self.dimensions)
        ]
        named += [
            (("observation_attributes", index, "keep_as"), attribute.keep_as)
            for index, attribute in enumerate(self.observation_attributes)
            if attribute.keep_as is not None
        ]
        seen: dict[str, Loc] = {}
        problems: list[Problem] = []
        for loc, column in named:
            if column in CANONICAL_COLUMNS:
                context = {"column": column, "reserved": ", ".join(CANONICAL_COLUMNS)}
                problems.append(Problem(COLUMN_RESERVED, loc, column, context))
            elif column in seen:
                context = {"column": column, "first": _where(seen[column][:-1])}
                problems.append(Problem(COLUMN_TWICE, loc, column, context))
            else:
                seen[column] = loc
        return problems

    def _selection_problems(self) -> list[Problem]:
        ref_area = self.series_metadata.ref_area
        if ref_area not in {d.id for d in self.dimensions}:
            return []  # reported once, at series_metadata.ref_area
        selectable = [d.id for d in self.dimensions if d.id != ref_area]
        problems: list[Problem] = []
        meant: set[str] = set()
        for key in self.selection:
            if key == ref_area:
                context = {"key": key}
                problems.append(
                    Problem(SELECTION_REF_AREA, ("selection", key), key, context)
                )
            elif key not in selectable:
                match = closest(key, (d for d in selectable if d not in self.selection))
                if match is not None:
                    meant.add(match)
                hint = f"; did you mean '{match}'?" if match else ""
                choices = ", ".join(selectable) or "none"
                context = {"key": key, "choices": choices, "hint": hint}
                problems.append(
                    Problem(SELECTION_NOT_SELECTABLE, ("selection", key), key, context)
                )
        selection = dict(self.selection)
        problems.extend(
            Problem(SELECTION_MISSING, ("selection",), selection, {"id": dimension})
            for dimension in selectable
            if dimension not in self.selection and dimension not in meant
        )
        return problems

    def _metadata_problems(self) -> list[Problem]:
        bound = self.series_metadata
        dimensions = {d.id: d for d in self.dimensions}
        series = {a.id for a in self.series_attributes}
        problems: list[Problem] = []
        area = dimensions.get(bound.ref_area)
        if area is None:
            hint = did_you_mean(bound.ref_area, dimensions)
            context = {"id": bound.ref_area, "hint": hint}
            loc = ("series_metadata", "ref_area")
            problems.append(Problem(NOT_A_DIMENSION, loc, bound.ref_area, context))
        elif not area.coded:
            loc = ("series_metadata", "ref_area")
            context = {"id": bound.ref_area}
            problems.append(Problem(REF_AREA_NOT_CODED, loc, bound.ref_area, context))
        loc = ("series_metadata", "frequency")
        if bound.frequency not in dimensions:
            context = {
                "id": bound.frequency,
                "hint": did_you_mean(bound.frequency, dimensions),
            }
            problems.append(Problem(NOT_A_DIMENSION, loc, bound.frequency, context))
        elif bound.frequency == bound.ref_area:
            context = {"id": bound.frequency}
            problems.append(
                Problem(FREQUENCY_IS_REF_AREA, loc, bound.frequency, context)
            )
        attributes = (
            ("title", bound.title),
            ("unit", bound.unit),
            ("decimals", bound.decimals),
        )
        for name, concept_id in attributes:
            if concept_id is not None and concept_id not in series:
                context = {"id": concept_id, "hint": did_you_mean(concept_id, series)}
                loc = ("series_metadata", name)
                problems.append(
                    Problem(NOT_A_SERIES_ATTRIBUTE, loc, concept_id, context)
                )
        return problems

    def _unit_multiplier_problems(self) -> list[Problem]:
        multiplier = self.measure.unit_multiplier
        coded = [a.id for a in self.series_attributes if a.coded]
        if multiplier is None or multiplier in coded:
            return []
        context = {"id": multiplier, "hint": did_you_mean(multiplier, coded)}
        loc = ("measure", "unit_multiplier")
        return [Problem(NOT_A_CODED_SERIES_ATTRIBUTE, loc, multiplier, context)]


class ColumnsSection(_ConfigModel):
    """How the concepts are written in the source file."""

    encoding: Annotated[str, AfterValidator(_check_encoding)]
    """Text encoding of the file, as Python names it, e.g. 'utf-8'."""
    # TODO(andrea-dm): reject a line break and '"' as the delimiter.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    delimiter: Annotated[str, Field(min_length=1, max_length=1)]
    """Field delimiter, one character."""
    header_label_separator: Text | None
    """Splits a header into concept id and label at its first occurrence; a header
    without it is all id. Null if headers carry no labels."""
    value_label_separator: Text | None
    """Splits a coded value into code and label at its first occurrence. Null if
    coded values carry no labels."""
    missing_values: UniqueTuple[str]
    """Measure texts that mean "no value"; such an observation is invalid."""
    # ELT: every column of the file, these included, is loaded into the raw layer as
    # it is; nothing is dropped on the way in. ``unmapped`` says only that staging never
    # maps the column to canonical data.
    unmapped: UniqueTuple[ConceptId]
    """Columns expected in the file but not declared in structure: loaded with the
    raw data, never mapped to canonical data, and not reported as unexpected."""
    optional: UniqueTuple[ConceptId]
    """Declared concepts whose column may be absent: a warning instead of an error.
    Every other declared concept is a required column."""


class RuleSpec(_ConfigModel):
    """One data-quality rule to run: which one, how serious, with what parameters."""

    name: RuleName
    """Name of the rule; a name the code does not implement is logged and skipped."""
    enabled: bool = True
    """False switches the rule off and keeps its settings."""
    severity: DataQualitySeverity
    """Severity of the rule's findings."""
    params: Mapping[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """The rule's parameters, checked by the rule itself."""


def _unique_rule_names(rules: tuple[RuleSpec, ...]) -> tuple[RuleSpec, ...]:
    first: dict[str, int] = {}
    problems: list[Problem] = []
    for index, rule in enumerate(rules):
        if rule.name in first:
            context = {"name": rule.name, "first": first[rule.name]}
            problems.append(Problem(RULE_TWICE, (index, "name"), rule.name, context))
        else:
            first[rule.name] = index
    raise_problems("rules", problems)
    return rules


RuleList = Annotated[tuple[RuleSpec, ...], AfterValidator(_unique_rule_names)]


class SnapshotSection(_ConfigModel):
    """Parameters of the snapshot calculation."""

    lookback_months: PositiveInt
    """Calendar months between a series' latest valid date and its target date."""
    stale_latest_days: PositiveInt
    """A latest observation older than this many days before the dataset's latest
    date is flagged STALE_LATEST."""
    stale_prior_days: PositiveInt
    """A prior observation older than this many days before the target date is
    flagged STALE_PRIOR."""


class OutputSection(_ConfigModel):
    """What the outputs carry."""

    metadata_fields: UniqueTuple[MetadataField]
    """SeriesMetadata fields added to each summary row, in column order."""


class DatasetConfig(_ConfigModel):
    """The validated configuration of one dataset."""

    model_config = ConfigDict(serialize_by_alias=True)

    schema_uri: str | None = Field(default=None, alias="$schema")
    """JSON Schema of this document, for editors. Ignored by the program."""
    dataset: DatasetSection
    """Identity and defaults."""
    source: SourceSection
    """Download settings: discovery page, file name, fallback URL, cache, attempts."""
    structure: StructureSection
    """The source's concepts and their roles."""
    columns: ColumnsSection
    """How the concepts are written in the file."""
    rules: RuleList
    """Data-quality rules to run, by name. Names are unique; the order in the file
    does not change the order the checks run."""
    snapshot: SnapshotSection
    """Snapshot parameters."""
    output: OutputSection
    """Output fields."""
    area_aliases: AreaAliases
    """Requested code to reference-area code. Keys are upper case; no alias maps to
    itself or to another alias."""

    def required_columns(self) -> tuple[str, ...]:
        """Returns the columns the file must have: declared and not optional.

        Returns:
            Concept ids in declaration order (see ``StructureSection.concepts``).
        """
        optional = set(self.columns.optional)
        return tuple(
            concept_id
            for _loc, concept_id, _role in self.structure.concepts()
            if concept_id not in optional
        )

    def known_columns(self) -> frozenset[str]:
        """Returns every column the file may have: declared or unmapped.

        Returns:
            Concept ids. A header outside this set is an unexpected column.
        """
        declared = {concept_id for _loc, concept_id, _role in self.structure.concepts()}
        return frozenset(declared | set(self.columns.unmapped))

    @model_validator(mode="after")
    def _check_sections(self) -> Self:
        problems = [
            *self._unmapped_problems(),
            *self._optional_problems(),
            *self._output_problems(),
        ]
        raise_problems(type(self).__name__, problems)
        return self

    def _unmapped_problems(self) -> list[Problem]:
        declared = {concept_id for _loc, concept_id, _role in self.structure.concepts()}
        by_case = {concept_id.casefold(): concept_id for concept_id in declared}
        problems: list[Problem] = []
        for index, concept_id in enumerate(self.columns.unmapped):
            loc = ("columns", "unmapped", index)
            if concept_id in declared:
                context = {"id": concept_id}
                problems.append(Problem(UNMAPPED_IS_DECLARED, loc, concept_id, context))
            elif (match := by_case.get(concept_id.casefold())) is not None:
                # Ids are case-sensitive, so "area" next to a declared "AREA" would be
                # a second, unmapped column: almost certainly a mistake, so rejected.
                context = {"id": concept_id, "declared": match}
                problems.append(Problem(UNMAPPED_CASE_CLASH, loc, concept_id, context))
        return problems

    def _needed_columns(self) -> dict[str, str]:
        """Maps each concept whose column the pipeline reads to the reason."""
        structure = self.structure
        needed: dict[str, str] = {}
        for _loc, concept_id, role in structure.concepts():
            if role == "dimension":
                needed.setdefault(concept_id, "a dimension")
        needed.setdefault(structure.time_dimension.id, "the time dimension")
        needed.setdefault(structure.measure.id, "the measure")
        if structure.measure.unit_multiplier is not None:
            needed.setdefault(structure.measure.unit_multiplier, "the unit multiplier")
        bound = structure.series_metadata
        for name, concept_id in (
            ("title", bound.title),
            ("unit", bound.unit),
            ("decimals", bound.decimals),
        ):
            if concept_id is not None:
                needed.setdefault(
                    concept_id, f"bound to structure.series_metadata.{name}"
                )
        for attribute in structure.observation_attributes:
            if attribute.keep_as is not None:
                needed.setdefault(attribute.id, "a kept observation attribute")
            if attribute.invalid_codes:
                needed.setdefault(attribute.id, "an attribute with invalid codes")
        return needed

    def _optional_problems(self) -> list[Problem]:
        declared = [concept_id for _loc, concept_id, _role in self.structure.concepts()]
        needed = self._needed_columns()
        problems: list[Problem] = []
        for index, concept_id in enumerate(self.columns.optional):
            loc = ("columns", "optional", index)
            if concept_id not in declared:
                context = {"id": concept_id, "hint": did_you_mean(concept_id, declared)}
                problems.append(Problem(OPTIONAL_UNDECLARED, loc, concept_id, context))
            elif concept_id in needed:
                context = {"id": concept_id, "role": needed[concept_id]}
                problems.append(Problem(OPTIONAL_IS_NEEDED, loc, concept_id, context))
        return problems

    def _output_problems(self) -> list[Problem]:
        bound = {
            "unit": self.structure.series_metadata.unit,
            "decimals": self.structure.series_metadata.decimals,
        }
        return [
            Problem(
                OUTPUT_FIELD_UNBOUND,
                ("output", "metadata_fields", index),
                name,
                {"field": name},
            )
            for index, name in enumerate(self.output.metadata_fields)
            if name in bound and bound[name] is None
        ]
