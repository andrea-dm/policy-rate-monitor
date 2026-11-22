"""The ``pysdmx``-backed reference source, with the client's queries replaced.

``tests/data/sdmx/`` holds BIS's answers to the three queries, recorded on 2026-11-22
and cut down to a few codes per code list. They are decoded by ``pysdmx`` itself, so
a change in how it reads them (such as reading BIS's time dimension) shows here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from msgspec import DecodeError
from msgspec.json import decode
from pysdmx.api.fmr import RegistryClient
from pysdmx.errors import InternalError, Invalid, NotFound, Unavailable

from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.config.models import ArtefactRef
from policy_rate_monitor.core.errors import ReferenceUnavailableError
from policy_rate_monitor.sdmx import SdmxReference

if TYPE_CHECKING:
    from policy_rate_monitor.config.models import ReferenceSection

pytestmark = pytest.mark.contract

ROOT = Path(__file__).parents[1]
RECORDED = ROOT / "tests" / "data" / "sdmx"
REFERENCE: ReferenceSection | None = (
    FileConfigSource(ROOT / "configs" / "bis_cbpol.json").load().reference
)


# The readers the client decodes the API's answers with.
READERS = RegistryClient("https://example.org/sdmx").deser


def recorded(name: str, kind: object) -> object:
    return decode((RECORDED / f"{name}.json").read_bytes(), type=kind).to_model()


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Answers the three queries with the recorded answers; returns the queries."""
    queries: list[tuple[str, ...]] = []

    def answer(name: str, kind: object) -> object:
        def query(_client: RegistryClient, *args: str) -> object:
            queries.append((name, *args))
            return recorded(name, kind)

        return query

    monkeypatch.setattr(
        RegistryClient, "get_dataflows", answer("dataflow", READERS.dataflows)
    )
    monkeypatch.setattr(
        RegistryClient,
        "get_data_structures",
        answer("datastructure", READERS.data_structures),
    )
    monkeypatch.setattr(RegistryClient, "get_codes", answer("codelist", READERS.codes))
    return queries


def source() -> SdmxReference:
    assert REFERENCE is not None
    return SdmxReference(REFERENCE)


def test_the_structure_is_the_one_the_dataflow_points_to(
    asked: list[tuple[str, ...]],
) -> None:
    structure = source().structure()

    assert asked == [
        ("dataflow", "BIS", "WS_CBPOL", "1.0"),
        ("datastructure", "BIS", "BIS_CBPOL", "1.0"),
    ]
    assert str(structure.structure) == "BIS:BIS_CBPOL(1.0)"
    roles = {component.id: component.role for component in structure.components}
    assert roles["FREQ"] == roles["REF_AREA"] == "dimension"
    assert roles["OBS_VALUE"] == "measure"
    assert roles["OBS_STATUS"] == roles["TITLE"] == "attribute"
    by_id = {component.id: component for component in structure.components}
    assert by_id["REF_AREA"].code_list == ArtefactRef(
        agency="BIS", id="CL_BIS_GL_REF_AREA", version="1.0"
    )
    assert not by_id["TITLE"].coded
    # BIS lists the time dimension under timeDimensions, which pysdmx 1.22.0 does not
    # read. If this fails, pysdmx reads it now: transform still leaves it out.
    assert "TIME_PERIOD" not in by_id


def test_the_codes_and_names_are_those_of_the_configured_code_list(
    asked: list[tuple[str, ...]],
) -> None:
    codes = source().ref_area_names()

    assert asked == [("codelist", "BIS", "CL_BIS_GL_REF_AREA", "1.0")]
    assert codes == {
        "00": "Others",
        "CH": "Switzerland",
        "GB": "United Kingdom",
        "JP": "Japan",
        "US": "United States",
        "XM": "Euro area",
    }


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            Unavailable("Connection error", "timed out"),
            "the SDMX API at https://stats.bis.org/api/v2 could not be reached",
        ),
        (
            NotFound("Not found", "404"),
            "the SDMX API has no dataflow BIS:WS_CBPOL(1.0)",
        ),
        (
            Invalid("Client error 400", "bad query"),
            (
                "the SDMX API answered the query for dataflow BIS:WS_CBPOL(1.0) "
                "with an error (Client error 400)"
            ),
        ),
        (
            InternalError("Service error 503", "down"),
            (
                "the SDMX API answered the query for dataflow BIS:WS_CBPOL(1.0) "
                "with an error (Service error 503)"
            ),
        ),
        (
            DecodeError("JSON is malformed"),
            "the SDMX API's answer for dataflow BIS:WS_CBPOL(1.0) cannot be read",
        ),
    ],
    ids=["unavailable", "not_found", "invalid", "internal", "undecodable"],
)
def test_each_failure_becomes_reference_unavailable_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    def fail(_client: RegistryClient, *_args: str) -> object:
        raise error

    monkeypatch.setattr(RegistryClient, "get_dataflows", fail)

    with pytest.raises(ReferenceUnavailableError) as raised:
        source().structure()

    assert raised.value.reason == reason


def test_a_missing_code_list_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_client: RegistryClient, *_args: str) -> object:
        raise NotFound("Not found", "no value list either")

    monkeypatch.setattr(RegistryClient, "get_codes", fail)

    with pytest.raises(ReferenceUnavailableError) as raised:
        source().ref_area_names()

    assert raised.value.reason == (
        "the SDMX API has no code list BIS:CL_BIS_GL_REF_AREA(1.0)"
    )


def test_any_other_error_is_a_bug_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_client: RegistryClient, *_args: str) -> object:
        raise KeyError("dataStructures")

    monkeypatch.setattr(RegistryClient, "get_codes", fail)

    with pytest.raises(KeyError):
        source().ref_area_names()
