"""Smoke test: the package imports and exposes its version."""

from __future__ import annotations

import pytest

import policy_rate_monitor


@pytest.mark.unit
def test_version_is_exposed() -> None:
    assert policy_rate_monitor.__version__ == "0.4.0"
