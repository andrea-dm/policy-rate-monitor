"""Tests of the JSON errors that ``parse_config`` reports before validation."""

from __future__ import annotations

import json

import pytest

from policy_rate_monitor.config import parse_config
from policy_rate_monitor.core.errors import ConfigError


@pytest.mark.unit
def test_invalid_json_gives_line_and_column() -> None:
    text = '{\n  "dataset": {\n    "id": "x"\n    "title": "y"\n  }\n}'

    with pytest.raises(ConfigError) as caught:
        parse_config(text, origin="fixture.json")

    assert str(caught.value).splitlines() == [
        "fixture.json: invalid JSON at line 4, column 5: Expecting ',' delimiter",
        '    4 |     "title": "y"',
        "      |     ^",
    ]
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


@pytest.mark.unit
def test_repeated_key_is_rejected() -> None:
    # Pydantic's own parser would silently keep the last value.
    with pytest.raises(ConfigError) as caught:
        parse_config('{"dataset": {}, "dataset": {}}', origin="fixture.json")

    assert str(caught.value) == "fixture.json: invalid JSON: duplicate key 'dataset'"
