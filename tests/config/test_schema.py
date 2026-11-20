"""Tests of the committed JSON Schema."""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from policy_rate_monitor.config.schema import REGENERATE_COMMAND, render_json_schema

COMMITTED = Path(__file__).resolve().parents[2] / "configs" / "config.schema.json"


@pytest.mark.contract
def test_committed_schema_is_current() -> None:
    committed = COMMITTED.read_text(encoding="ascii")
    current = render_json_schema()

    if committed != current:
        diff = difflib.unified_diff(
            committed.splitlines(),
            current.splitlines(),
            "configs/config.schema.json",
            "generated",
            lineterm="",
        )
        shown = "\n".join(list(diff)[:20])
        message = f"the committed schema is out of date; run:\n  {REGENERATE_COMMAND}"
        pytest.fail(f"{message}\n{shown}")
