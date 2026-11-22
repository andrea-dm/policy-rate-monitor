"""The SDMX rules of ``transform``, run through a fake reference source."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from policy_rate_monitor.config.models import ArtefactRef
from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.reference import (
    NO_REFERENCE,
    DataflowStructure,
    ReferenceSource,
    StructureComponent,
)
from policy_rate_monitor.transform import (
    load_data_quality,
    load_observations,
    transform,
)
from tests.fake_reference import FakeReference
from tests.test_transform import GB_ROW, US, US_SERIES, make_config, write_zip

if TYPE_CHECKING:
    from pathlib import Path

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.core.models import DataQualityIssue

pytestmark = pytest.mark.contract

REF_AREA_CODES = ArtefactRef(agency="ORG", id="CL_AREA", version="1.0")
REFERENCE = {
    "api_url": "https://example.org/sdmx",
    "dataflow": {"agency": "ORG", "id": "RATES", "version": "1.0"},
    "ref_area_code_list": {"agency": "ORG", "id": "CL_AREA", "version": "1.0"},
    "timeout_seconds": 5,
}
SDMX_RULES = [
    {"name": "ref_area_not_in_code_list", "severity": "warning"},
    {"name": "dataflow_mismatch", "severity": "error"},
]


def coded(component_id: str, role: str = "attribute") -> StructureComponent:
    code_list = ArtefactRef(agency="ORG", id=f"CL_{component_id}", version="1.0")
    return StructureComponent(id=component_id, role=role, code_list=code_list)  # type: ignore[arg-type]


# The definition the test configuration describes (test_transform.CONFIG).
MATCHING = DataflowStructure(
    structure=ArtefactRef(agency="ORG", id="DSD_RATES", version="1.0"),
    components=(
        coded("FREQ", "dimension"),
        StructureComponent(id="REF_AREA", role="dimension", code_list=REF_AREA_CODES),
        StructureComponent(id="OBS_VALUE", role="measure", code_list=None),
        coded("OBS_STATUS"),
        coded("UNIT_MEASURE"),
        coded("UNIT_MULT"),
        coded("DECIMALS"),
        StructureComponent(id="TITLE", role="attribute", code_list=None),
    ),
)
US_ROW = f"{US},2024-01-01,5.25,{US_SERIES},A: Normal"


def sdmx_config(
    rules: list[dict[str, Any]] | None = None, **changes: object
) -> DatasetConfig:
    return make_config(
        reference=REFERENCE, rules=SDMX_RULES if rules is None else rules, **changes
    )


def sdmx_findings(data_dir: Path) -> list[DataQualityIssue]:
    return [
        issue
        for issue in load_data_quality(data_dir)
        if issue.rule in {"ref_area_not_in_code_list", "dataflow_mismatch"}
    ]


def with_components(*components: StructureComponent) -> DataflowStructure:
    return replace(MATCHING, components=components)


def test_a_matching_reference_gives_no_finding_also_for_labelled_codes(
    tmp_path: Path,
) -> None:
    # The file writes each code with its label ("US: United States").
    write_zip(tmp_path, US_ROW, GB_ROW)
    reference = FakeReference(MATCHING, frozenset({"US", "GB"}))

    transform(sdmx_config(), tmp_path, reference)

    assert sdmx_findings(tmp_path) == []
    assert sorted(reference.calls) == ["ref_area_codes", "structure"]


def test_a_code_not_in_the_code_list_is_one_finding_per_series(
    tmp_path: Path,
) -> None:
    write_zip(tmp_path, US_ROW, GB_ROW, GB_ROW.replace("2024-01-01", "2024-01-02"))
    without = tmp_path / "without"
    write_zip(without, US_ROW, GB_ROW, GB_ROW.replace("2024-01-01", "2024-01-02"))
    transform(sdmx_config(rules=[]), without)

    transform(sdmx_config(), tmp_path, FakeReference(MATCHING, frozenset({"US"})))

    (finding,) = sdmx_findings(tmp_path)
    assert finding.rule == "ref_area_not_in_code_list"
    assert (finding.series_key, finding.count) == ("D.GB", 2)
    assert "REF_AREA code is not in the code list" in finding.detail
    # No row is dropped or changed.
    assert load_observations(tmp_path).equals(load_observations(without))


@pytest.mark.parametrize(
    ("definition", "named"),
    [
        (
            with_components(*MATCHING.components[:-1]),
            "TITLE is not in the definition",
        ),
        (
            with_components(
                *MATCHING.components,
                StructureComponent(id="COMMENT", role="attribute", code_list=None),
            ),
            "COMMENT is in the definition, not in the configuration",
        ),
        (
            with_components(
                *MATCHING.components[:3],
                coded("OBS_STATUS", "dimension"),
                *MATCHING.components[4:],
            ),
            (
                "OBS_STATUS is a dimension in the definition, an attribute in the "
                "configuration"
            ),
        ),
        (
            with_components(*MATCHING.components[:-1], coded("TITLE")),
            "TITLE is coded in the definition, not coded in the configuration",
        ),
        (
            with_components(
                coded("FREQ", "dimension"),
                coded("REF_AREA", "dimension"),
                *MATCHING.components[2:],
            ),
            (
                "REF_AREA is coded by ORG:CL_REF_AREA(1.0) in the definition, "
                "not by the configured ORG:CL_AREA(1.0)"
            ),
        ),
    ],
    ids=["missing", "extra", "role", "coded", "code_list"],
)
def test_each_structure_difference_is_one_dataset_finding_naming_it(
    tmp_path: Path, definition: DataflowStructure, named: str
) -> None:
    write_zip(tmp_path, US_ROW)

    transform(sdmx_config(), tmp_path, FakeReference(definition, frozenset({"US"})))

    (finding,) = sdmx_findings(tmp_path)
    assert finding.rule == "dataflow_mismatch"
    assert (finding.series_key, finding.count) == (None, 1)
    assert finding.detail == (
        f"1 difference from the definition ORG:DSD_RATES(1.0): {named}"
    )


def test_an_unmapped_column_and_the_time_dimension_are_not_differences(
    tmp_path: Path,
) -> None:
    write_zip(tmp_path, US_ROW)
    definition = with_components(
        *MATCHING.components,
        coded("TIME_FORMAT"),
        StructureComponent(id="TIME_PERIOD", role="dimension", code_list=None),
    )
    columns = {**make_config().columns.model_dump(), "unmapped": ["TIME_FORMAT"]}

    transform(
        sdmx_config(columns=columns),
        tmp_path,
        FakeReference(definition, frozenset({"US"})),
    )

    assert sdmx_findings(tmp_path) == []


@pytest.mark.parametrize(
    ("reference", "reason"),
    [
        (
            FakeReference(reason="the SDMX API could not be reached"),
            "the SDMX API could not be reached",
        ),
        (NO_REFERENCE, "no SDMX reference source was given"),
    ],
    ids=["unreachable", "default"],
)
def test_an_unavailable_reference_gives_one_not_evaluated_finding_per_rule(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    reference: ReferenceSource,
    reason: str,
) -> None:
    write_zip(tmp_path, US_ROW, GB_ROW)
    without = tmp_path / "without"
    write_zip(without, US_ROW, GB_ROW)
    transform(sdmx_config(rules=[]), without)

    with caplog.at_level(logging.WARNING, logger="policy_rate_monitor.transform"):
        summary = transform(sdmx_config(), tmp_path, reference)

    findings = sdmx_findings(tmp_path)
    assert [(f.rule, f.severity, f.series_key, f.count) for f in findings] == [
        ("ref_area_not_in_code_list", "warning", None, 0),
        ("dataflow_mismatch", "error", None, 0),
    ]
    assert {f.detail for f in findings} == {f"not evaluated: {reason}"}
    assert summary.issues == 2
    assert load_observations(tmp_path).equals(load_observations(without))
    (warning,) = [line for line in caplog.messages if "not evaluated" in line]
    assert "ref_area_not_in_code_list, dataflow_mismatch" in warning
    assert reason in warning


def test_rules_that_are_off_never_ask_the_reference(tmp_path: Path) -> None:
    write_zip(tmp_path, US_ROW)
    reference = FakeReference(MATCHING, frozenset({"US"}))
    rules = [{**rule, "enabled": False} for rule in SDMX_RULES]

    transform(sdmx_config(rules=rules), tmp_path, reference)

    assert reference.calls == []
    assert sdmx_findings(tmp_path) == []


def test_a_missing_zip_ends_before_the_reference_is_asked(tmp_path: Path) -> None:
    reference = FakeReference(MATCHING, frozenset({"US"}))

    with pytest.raises(MissingInputError):
        transform(sdmx_config(), tmp_path, reference)

    assert reference.calls == []
