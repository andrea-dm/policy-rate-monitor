"""Message templates for the configuration checks, and how a check reports them.

A check in ``config.models`` reports a single problem by raising ``Template.error``,
or several at once through ``raise_problems``: one ``ValidationError`` with one error
per problem, each at its exact location, all in the same pass. pydantic takes a
message template as a ``LiteralString``, so each template is a fixed string whose
``{name}`` fields are filled from a context mapping.

Examples:
    >>> from pydantic import ValidationError
    >>> problem = Problem(ALIAS_TO_ITSELF, ("Q1",), "Q1", {"code": "Q1"})
    >>> try:
    ...     raise_problems("Example", [problem])
    ... except ValidationError as error:
    ...     print(error.errors()[0]["loc"], error.errors()[0]["msg"])
    ('Q1',) 'Q1' maps to itself
    >>> did_you_mean("GEOS", ["GEO", "FRQ"])
    "; did you mean 'GEO'?"
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, LiteralString

from pydantic import ValidationError
from pydantic_core import PydanticCustomError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "KINDS",
    "TEMPLATES",
    "Loc",
    "Problem",
    "Template",
    "closest",
    "did_you_mean",
    "raise_problems",
]

type Loc = tuple[str | int, ...]
"""A location inside the configuration document, as pydantic writes it."""


@dataclass(frozen=True, slots=True)
class Template:
    """A fixed message with ``{name}`` fields, and the error type it reports.

    Attributes:
        kind: The pydantic error type, e.g. ``"alias_to_itself"``.
        text: The message template, e.g. ``"'{code}' maps to itself"``.
    """

    kind: LiteralString
    text: LiteralString

    def error(self, **context: str | int) -> PydanticCustomError:
        """Returns the error a validator raises for one problem.

        Args:
            **context: The values of the template's ``{name}`` fields.

        Returns:
            The error, ready to raise.
        """
        return PydanticCustomError(self.kind, self.text, dict(context))


@dataclass(frozen=True, slots=True)
class Problem:
    """One problem found by a check that reports several at once.

    Attributes:
        template: What went wrong.
        loc: Where, relative to the model or field the check runs on.
        value: The offending input.
        context: The values of the template's ``{name}`` fields.
    """

    template: Template
    loc: Loc
    value: object
    context: Mapping[str, str | int] = field(default_factory=dict[str, str | int])


def raise_problems(title: str, problems: Sequence[Problem]) -> None:
    """Raises one ``ValidationError`` carrying every problem, if there is any.

    Args:
        title: The name pydantic gives the error, usually the model's.
        problems: The problems found, in the order they are reported.

    Raises:
        ValidationError: *problems* is not empty.
    """
    if not problems:
        return
    # TODO(andrea-dm): drop the title: inside a validator pydantic-core replaces it.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    raise ValidationError.from_exception_data(
        title,
        [
            {
                "type": problem.template.error(**problem.context),
                "loc": problem.loc,
                "input": problem.value,
            }
            for problem in problems
        ],
    )


def closest(name: str, candidates: Iterable[str]) -> str | None:
    """Returns the candidate closest to *name*, or ``None`` if none is close.

    Args:
        name: The name that matched nothing.
        candidates: The names it could have meant.

    Returns:
        A candidate that differs from *name* only in case, else the closest one by
        ``difflib`` similarity of at least 0.6.
    """
    choices = list(candidates)
    # Ids are case-sensitive, so "geo" matches no declared "GEO"; it is still the
    # likeliest meaning, which difflib's case-sensitive ratio would miss.
    folded = name.casefold()
    for choice in choices:
        if choice.casefold() == folded:
            return choice
    matches = difflib.get_close_matches(name, choices, n=1, cutoff=0.6)
    return matches[0] if matches else None


def did_you_mean(name: str, candidates: Iterable[str]) -> str:
    """Returns ``"; did you mean 'X'?"`` for the closest candidate, or ``""``.

    Args:
        name: The name that matched nothing.
        candidates: The names it could have meant.

    Returns:
        The hint, ready to append to a message.
    """
    match = closest(name, candidates)
    return f"; did you mean '{match}'?" if match else ""


ALIAS_NOT_UPPER_CASE: Final = Template(
    "alias_not_upper_case",
    "'{code}' is not upper case; requested codes are upper-cased before aliases "
    "apply, so it would never match",
)
ALIAS_TO_ITSELF: Final = Template("alias_to_itself", "'{code}' maps to itself")
ALIAS_CHAIN: Final = Template(
    "alias_chain",
    "'{code}' maps to '{target}', which is itself an alias: map '{code}' to the "
    "final code",
)
RULE_TWICE: Final = Template(
    "rule_twice", "rule '{name}' is configured twice (first at rules[{first}])"
)
LISTED_TWICE: Final = Template(
    "listed_twice", "'{value}' is listed twice (first at [{first}])"
)
PATH_EMPTY: Final = Template(
    "path_empty", "the path is empty: give a directory, e.g. 'data'"
)
PATH_TILDE: Final = Template(
    "path_tilde",
    "'{path}' starts with '~', which is not expanded: give an absolute path or one "
    "relative to the working directory",
)
PATH_DOLLAR: Final = Template(
    "path_dollar",
    "'{path}' contains '$', which is not expanded: give the path itself",
)
DATE_FORMAT_NO_ROUND_TRIP: Final = Template(
    "date_format_no_round_trip",
    "'{format}' does not round-trip a date: it must give the year, month and day",
)
UNKNOWN_ENCODING: Final = Template(
    "unknown_encoding", "'{encoding}' is not a known text encoding"
)
FALLBACK_URL_NAME: Final = Template(
    "fallback_url_name",
    "the URL ends in '{last}', not in file_name '{file_name}'",
)
SCALE_ABOVE_PRECISION: Final = Template(
    "scale_above_precision", "scale {scale} exceeds precision {precision}"
)
INVALID_CODES_NOT_CODED: Final = Template(
    "invalid_codes_not_coded",
    "invalid codes need a coded attribute; '{id}' is not coded",
)
CONCEPT_TWICE: Final = Template(
    "concept_twice", "'{id}' is already declared at {first}"
)
CONCEPT_CASE_CLASH: Final = Template(
    "concept_case_clash",
    "'{id}' is already declared at {first} ('{first_id}'); ids must differ in more "
    "than case",
)
COLUMN_RESERVED: Final = Template(
    "column_reserved", "'{column}' is a fixed canonical column ({reserved})"
)
COLUMN_TWICE: Final = Template(
    "column_twice", "'{column}' is already the column of {first}"
)
SELECTION_NOT_SELECTABLE: Final = Template(
    "selection_not_selectable",
    "'{key}' is not a dimension that can be selected ({choices}){hint}",
)
SELECTION_REF_AREA: Final = Template(
    "selection_ref_area",
    "'{key}' is the reference-area dimension: its codes are chosen per request, "
    "not here",
)
SELECTION_MISSING: Final = Template(
    "selection_missing",
    "dimension '{id}' has no selected code, so one country or area could have "
    "several series",
)
NOT_A_DIMENSION: Final = Template(
    "not_a_dimension", "'{id}' is not a declared dimension{hint}"
)
REF_AREA_NOT_CODED: Final = Template(
    "ref_area_not_coded",
    "'{id}' is not coded, so it has no label for ref_area_name",
)
FREQUENCY_IS_REF_AREA: Final = Template(
    "frequency_is_ref_area", "'{id}' is already the ref_area dimension"
)
NOT_A_SERIES_ATTRIBUTE: Final = Template(
    "not_a_series_attribute", "'{id}' is not a declared series attribute{hint}"
)
NOT_A_CODED_SERIES_ATTRIBUTE: Final = Template(
    "not_a_coded_series_attribute",
    "'{id}' is not a coded series attribute{hint}",
)
UNMAPPED_IS_DECLARED: Final = Template(
    "unmapped_is_declared",
    "'{id}' is declared in structure, so it is mapped and cannot be listed as unmapped",
)
UNMAPPED_CASE_CLASH: Final = Template(
    "unmapped_case_clash",
    "'{id}' differs from the declared concept '{declared}' only in case",
)
OPTIONAL_UNDECLARED: Final = Template(
    "optional_undeclared", "'{id}' is not a declared concept{hint}"
)
OPTIONAL_IS_NEEDED: Final = Template(
    "optional_is_needed", "'{id}' is {role}: its column is required"
)
OUTPUT_FIELD_UNBOUND: Final = Template(
    "output_field_unbound",
    "'{field}' would always be empty: structure.series_metadata.{field} is null",
)

TEMPLATES: Final = (
    ALIAS_NOT_UPPER_CASE,
    ALIAS_TO_ITSELF,
    ALIAS_CHAIN,
    RULE_TWICE,
    LISTED_TWICE,
    PATH_EMPTY,
    PATH_TILDE,
    PATH_DOLLAR,
    DATE_FORMAT_NO_ROUND_TRIP,
    UNKNOWN_ENCODING,
    FALLBACK_URL_NAME,
    SCALE_ABOVE_PRECISION,
    INVALID_CODES_NOT_CODED,
    CONCEPT_TWICE,
    CONCEPT_CASE_CLASH,
    COLUMN_RESERVED,
    COLUMN_TWICE,
    SELECTION_NOT_SELECTABLE,
    SELECTION_REF_AREA,
    SELECTION_MISSING,
    NOT_A_DIMENSION,
    REF_AREA_NOT_CODED,
    FREQUENCY_IS_REF_AREA,
    NOT_A_SERIES_ATTRIBUTE,
    NOT_A_CODED_SERIES_ATTRIBUTE,
    UNMAPPED_IS_DECLARED,
    UNMAPPED_CASE_CLASH,
    OPTIONAL_UNDECLARED,
    OPTIONAL_IS_NEEDED,
    OUTPUT_FIELD_UNBOUND,
)
"""Every template, so the parser can tell this package's errors from pydantic's."""

KINDS: Final = frozenset(template.kind for template in TEMPLATES)
"""The error types of this package's checks. Their messages already quote the input."""
