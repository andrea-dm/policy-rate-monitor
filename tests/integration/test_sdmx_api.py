"""The committed configuration checked against the live BIS SDMX API.

Left out of the default run (``-m "not integration"``): it needs the network and BIS's
service. Run it with ``pytest -m integration`` to see whether BIS has changed the
dataflow definition or the code list since the configuration was written. The tests
of the default run use a fake source and a recorded answer, which cannot see that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.config.models import SDMX_RULES
from policy_rate_monitor.sdmx import SdmxReference
from policy_rate_monitor.transform import load_data_quality, transform
from tests.bis_zip import write_bis_zip

pytestmark = pytest.mark.integration

CONFIG = Path(__file__).parents[2] / "configs" / "bis_cbpol.json"


def test_the_bis_reference_still_matches_the_configuration(tmp_path: Path) -> None:
    config = FileConfigSource(CONFIG).load()
    assert config.reference is not None
    write_bis_zip(tmp_path, config.source.file_name)

    transform(config, tmp_path, SdmxReference(config.reference))

    sdmx_findings = [
        issue for issue in load_data_quality(tmp_path) if issue.rule in SDMX_RULES
    ]
    assert sdmx_findings == []


def test_every_configured_suggestion_is_a_code_of_the_bis_list() -> None:
    config = FileConfigSource(CONFIG).load()
    assert config.reference is not None

    names = SdmxReference(config.reference).ref_area_names()

    assert names["GB"] == "United Kingdom"
    assert set(config.ref_area_suggestions.values()) <= set(names)
    # A suggestion is only looked up for a code the list does not have.
    assert not set(config.ref_area_suggestions) & set(names)
