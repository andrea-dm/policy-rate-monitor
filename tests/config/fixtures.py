"""A fixture configuration of a fictitious dataset, and helpers to vary it.

No name here is a real dataset's: the tests show that nothing in ``config`` is
written for one source. Every call returns a fresh document, so a test can change it
freely.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from policy_rate_monitor.config import DatasetConfig, parse_config
from policy_rate_monitor.core.errors import ConfigError

type Document = dict[str, Any]

ORIGIN = "fixture.json"

_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def minimal_document() -> Document:
    """Returns a valid configuration of a fictitious two-dimension dataset."""
    return {
        "dataset": {
            "id": "example_rates",
            "title": "Example rates",
            "description": "A fictitious dataset for the tests.",
            "attribution": "Source: Example Org",
            "data_dir": "data",
        },
        "source": {
            "discovery_url": "https://example.org/downloads",
            "file_name": "example.zip",
            "fallback_url": "https://example.org/files/example.zip",
            "cache_ttl_hours": 24,
            "max_attempts": 3,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 30.5,
        },
        "structure": {
            "dimensions": [
                {"id": "FRQ", "coded": True, "column": "frq"},
                {"id": "AREA", "coded": True, "column": "area"},
            ],
            "time_dimension": {"id": "DAY", "date_format": "%Y-%m-%d"},
            "measure": {
                "id": "VAL",
                "precision": 12,
                "scale": 4,
                "unit_multiplier": "MULT",
            },
            "observation_attributes": [
                {"id": "STS", "coded": True, "keep_as": "sts", "invalid_codes": ["X"]},
                {"id": "NOTE", "coded": False, "keep_as": None},
            ],
            "series_attributes": [
                {"id": "TTL", "coded": False},
                {"id": "UNT", "coded": True},
                {"id": "MULT", "coded": True},
                {"id": "DEC", "coded": True},
                {"id": "INFO", "coded": False},
            ],
            "series_metadata": {
                "ref_area": "AREA",
                "frequency": "FRQ",
                "title": "TTL",
                "unit": "UNT",
                "decimals": "DEC",
            },
            "selection": {"FRQ": "D"},
        },
        "columns": {
            "encoding": "utf-8",
            "delimiter": ";",
            "header_label_separator": ":",
            "value_label_separator": ": ",
            "missing_values": ["NA"],
            "unmapped": ["FLOW"],
            "optional": ["INFO"],
        },
        "rules": [
            {"name": "first_rule", "severity": "warning"},
            {
                "name": "second_rule",
                "enabled": False,
                "severity": "info",
                "params": {"limit": 5, "nested": {"codes": ["a", "b"]}},
            },
        ],
        "snapshot": {
            "lookback_months": 12,
            "stale_latest_days": 30,
            "stale_prior_days": 7,
        },
        "output": {
            "metadata_fields": [
                "series_key",
                "ref_area_name",
                "title",
                "unit",
                "decimals",
            ]
        },
        "area_aliases": {"Q1": "Q2"},
    }


def changed(document: Document, path: str, value: object) -> Document:
    """Returns a copy of *document* with the value at *path* set, e.g. ``"a.b[0].c"``.

    The last part may name a key that does not exist yet.
    """
    result = copy.deepcopy(document)
    *parents, last = [
        int(index) if index else key for key, index in _PATH_PART.findall(path)
    ]
    target: Any = result
    for part in parents:
        target = target[part]
    target[last] = value
    return result


def load(document: Document) -> DatasetConfig:
    """Validates *document* through ``parse_config``."""
    return parse_config(json.dumps(document), origin=ORIGIN)


def failure(document: Document) -> ConfigError:
    """Returns the ``ConfigError`` that validating *document* raises."""
    with pytest.raises(ConfigError) as caught:
        load(document)
    return caught.value


def error_lines(document: Document) -> list[str]:
    """Returns the problem lines of the error, without the header and indentation."""
    return [line.strip() for line in str(failure(document)).splitlines()[1:]]
