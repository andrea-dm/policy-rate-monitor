"""Tests of the configuration models, through the parser every source uses."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tests.config.fixtures import changed, error_lines, load, minimal_document


@pytest.mark.unit
def test_unknown_key_is_rejected_with_its_location() -> None:
    document = changed(minimal_document(), "snapshot.unknown", 1)

    assert error_lines(document) == [
        "snapshot.unknown: Extra inputs are not permitted (got 1)"
    ]


@pytest.mark.unit
def test_every_failing_field_is_listed() -> None:
    document = changed(minimal_document(), "source.max_attempts", "3")
    document = changed(document, "structure.measure.precision", 16)

    assert error_lines(document) == [
        'source.max_attempts: Input should be a valid integer (got "3")',
        (
            "structure.measure.precision: Input should be less than or equal to 15"
            " (got 16)"
        ),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (
            "structure.selection",
            {"FRQX": "D"},
            (
                "structure.selection.FRQX: 'FRQX' is not a dimension that can be"
                " selected (FRQ); did you mean 'FRQ'?"
            ),
        ),
        (
            "structure.series_metadata.title",
            "TTLX",
            (
                "structure.series_metadata.title: 'TTLX' is not a declared series"
                " attribute; did you mean 'TTL'?"
            ),
        ),
    ],
    ids=["selection", "series_metadata"],
)
def test_reference_to_an_undeclared_concept_is_rejected(
    path: str, value: object, expected: str
) -> None:
    assert error_lines(changed(minimal_document(), path, value)) == [expected]


@pytest.mark.unit
def test_output_field_must_be_a_series_metadata_field() -> None:
    document = changed(
        minimal_document(), "output.metadata_fields", ["series_key", "TTL"]
    )

    assert error_lines(document) == [
        (
            "output.metadata_fields[1]: Input should be 'series_key', 'ref_area',"
            " 'ref_area_name', 'frequency', 'title', 'unit' or 'decimals' (got \"TTL\")"
        )
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("aliases", "expected"),
    [
        ({"Q1": "Q1"}, "ref_area_aliases.Q1: 'Q1' maps to itself"),
        (
            {"Q1": "Q2", "Q2": "Q3"},
            (
                "ref_area_aliases.Q1: 'Q1' maps to 'Q2', which is itself an alias:"
                " map 'Q1' to the final code"
            ),
        ),
    ],
    ids=["to_itself", "chain"],
)
def test_alias_must_map_to_a_final_code(aliases: dict[str, str], expected: str) -> None:
    document = changed(minimal_document(), "ref_area_aliases", aliases)

    assert error_lines(document) == [expected]


@pytest.mark.unit
def test_two_digit_year_date_format_is_rejected() -> None:
    # "%y" would read 1950 as 2050; a policy-rate history starts in the 1940s.
    document = changed(
        minimal_document(), "structure.time_dimension.date_format", "%y-%m-%d"
    )

    assert error_lines(document) == [
        (
            "structure.time_dimension.date_format: '%y-%m-%d' does not round-trip a"
            " date: it must give the year, month and day"
        )
    ]


@pytest.mark.unit
def test_id_differing_from_a_declared_one_only_in_case_is_rejected() -> None:
    document = changed(minimal_document(), "columns.unmapped", ["frq"])

    assert error_lines(document) == [
        (
            "columns.unmapped[0]: 'frq' differs from the declared concept 'FRQ' only"
            " in case"
        )
    ]


@pytest.mark.unit
def test_dollar_schema_key_is_accepted() -> None:
    document = changed(minimal_document(), "$schema", "./config.schema.json")

    assert load(document).schema_uri == "./config.schema.json"


@pytest.mark.unit
def test_models_are_frozen() -> None:
    config = load(minimal_document())
    selection: Any = config.structure.selection

    with pytest.raises(ValidationError, match="frozen"):
        setattr(config.snapshot, "lookback_months", 6)  # noqa: B010
    with pytest.raises(TypeError, match="does not support item assignment"):
        selection["FRQ"] = "M"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (
            {
                "name": "conflicting_duplicate",
                "severity": "warning",
                "params": {"keep": "First"},
            },
            (
                'rules[0] ("conflicting_duplicate").params.keep: rule'
                " 'conflicting_duplicate' needs params.keep to be 'first' or 'last',"
                ' not "First"'
            ),
        ),
        (
            {"name": "unexpected_value", "severity": "warning", "params": {}},
            (
                'rules[0] ("unexpected_value").params.expected: rule'
                " 'unexpected_value' needs params.expected to be an object of column"
                " ids and their codes, not null"
            ),
        ),
        (
            {
                "name": "unexpected_value",
                "severity": "warning",
                "params": {"expected": {"TTLX": "A"}},
            },
            (
                "rules[0] (\"unexpected_value\").params.expected.TTLX: 'TTLX' is not"
                " a column of the configuration; did you mean 'TTL'?"
            ),
        ),
        (
            {
                "name": "unexpected_value",
                "severity": "warning",
                "params": {"expected": {"TTL": ["A", "B"]}},
            },
            (
                'rules[0] ("unexpected_value").params.expected.TTL: rule'
                " 'unexpected_value' needs params.expected.TTL to be a text, not"
                ' ["A", "B"]'
            ),
        ),
    ],
    ids=["keep", "expected_missing", "expected_unknown_id", "expected_not_a_code"],
)
def test_rule_parameters_are_checked_even_when_the_rule_is_off(
    rule: dict[str, Any], expected: str
) -> None:
    document = changed(minimal_document(), "rules", [{**rule, "enabled": False}])

    assert error_lines(document) == [expected]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rules", "keep"),
    [
        ([], "last"),
        ([{"name": "conflicting_duplicate", "severity": "info"}], "last"),
        (
            [
                {
                    "name": "conflicting_duplicate",
                    "severity": "info",
                    "enabled": False,
                    "params": {"keep": "first"},
                }
            ],
            "first",
        ),
    ],
    ids=["no_rule", "no_keep", "rule_off"],
)
def test_conflict_keep_applies_whether_the_rule_is_on_or_not(
    rules: list[dict[str, Any]], keep: str
) -> None:
    assert load(changed(minimal_document(), "rules", rules)).conflict_keep() == keep


REFERENCE = {
    "api_url": "https://example.org/sdmx",
    "dataflow": {"agency": "ORG", "id": "FLOW", "version": "1.0"},
    "ref_area_code_list": {"agency": "ORG", "id": "CL_GEO", "version": "1.0"},
    "timeout_seconds": 5,
}


@pytest.mark.unit
def test_a_configuration_without_a_reference_section_loads() -> None:
    assert load(minimal_document()).reference is None


@pytest.mark.unit
def test_the_reference_section_is_read() -> None:
    reference = load(changed(minimal_document(), "reference", REFERENCE)).reference

    assert reference is not None
    assert str(reference.dataflow) == "ORG:FLOW(1.0)"
    assert str(reference.ref_area_code_list) == "ORG:CL_GEO(1.0)"
    assert reference.timeout_seconds == 5


@pytest.mark.unit
@pytest.mark.parametrize("name", ["ref_area_not_in_code_list", "dataflow_mismatch"])
@pytest.mark.parametrize("enabled", [True, False])
def test_an_sdmx_rule_needs_the_reference_section_even_when_off(
    name: str, *, enabled: bool
) -> None:
    rule = {"name": name, "severity": "warning", "enabled": enabled}
    document = changed(minimal_document(), "rules", [rule])

    assert error_lines(document) == [
        (
            f"rules[0] (\"{name}\").name: rule '{name}' checks the data against"
            " the SDMX reference: add a reference section"
        )
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (
            "reference.dataflow.id",
            "",
            'reference.dataflow.id: String should have at least 1 character (got "")',
        ),
        (
            "reference.timeout_seconds",
            0,
            "reference.timeout_seconds: Input should be greater than 0 (got 0)",
        ),
        (
            "reference.ref_area_code_list.agency",
            "",
            (
                "reference.ref_area_code_list.agency: String should have at least 1"
                ' character (got "")'
            ),
        ),
    ],
    ids=["empty_id", "zero_timeout", "empty_agency"],
)
def test_a_bad_reference_field_is_rejected(
    path: str, value: object, expected: str
) -> None:
    document = changed(changed(minimal_document(), "reference", REFERENCE), path, value)

    assert error_lines(document) == [expected]
