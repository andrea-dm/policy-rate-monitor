"""Shared pytest configuration: doctest markers (no fixtures yet)."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Marks every doctest as ``unit``.

    Doctests carry no markers, so ``pytest -m unit`` would silently deselect them.
    They are quick and do no I/O, which is what ``unit`` means. The class is matched
    by name: ``DoctestItem`` is not part of pytest's public API.

    Args:
        items: Every collected test item.
    """
    for item in items:
        if type(item).__name__ == "DoctestItem":
            item.add_marker(pytest.mark.unit)
