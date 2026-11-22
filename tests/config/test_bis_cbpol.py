"""Tests of the committed policy-rate configuration, the only reader of that file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from policy_rate_monitor.config import DatasetConfig, FileConfigSource

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "bis_cbpol.json"
CONFIG_PACKAGE = REPO_ROOT / "src" / "policy_rate_monitor" / "config"


@pytest.fixture(scope="module")
def bis_cbpol() -> tuple[DatasetConfig, dict[str, Any]]:
    """The validated configuration and its raw JSON, read once for the module."""
    document: dict[str, Any] = json.loads(CONFIG.read_text(encoding="utf-8"))
    return FileConfigSource(CONFIG).load(), document


@pytest.mark.contract
def test_bis_cbpol_loads(bis_cbpol: tuple[DatasetConfig, dict[str, Any]]) -> None:
    config, _document = bis_cbpol

    assert config.structure.selection == {"FREQ": "D"}
    assert config.ref_area_aliases == {"EA": "XM"}
    assert len(config.known_columns()) == 18


@pytest.mark.contract
def test_config_package_holds_no_dataset_facts(
    bis_cbpol: tuple[DatasetConfig, dict[str, Any]],
) -> None:
    config, document = bis_cbpol
    concept_ids = [
        concept_id for _loc, concept_id, _role in config.structure.concepts()
    ]
    facts = [
        document["source"]["discovery_url"],
        document["source"]["fallback_url"],
        config.source.file_name,
        *concept_ids,
        # TODO(andrea-dm): add the dataset id, the source host and the dataflow code.
        # https://github.com/andrea-dm/policy-rate-monitor/issues/8
        *config.columns.unmapped,
    ]
    patterns = {
        fact: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(fact)}(?![A-Za-z0-9_])")
        for fact in facts
    }

    found = [
        f"{path.relative_to(REPO_ROOT)}: {fact}"
        for path in sorted(CONFIG_PACKAGE.rglob("*.py"))
        for fact, pattern in patterns.items()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert found == []
