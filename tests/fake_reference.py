"""A reference source for the tests: answers set in advance, calls recorded."""

from __future__ import annotations

from typing import override

from policy_rate_monitor.core.errors import ReferenceUnavailableError
from policy_rate_monitor.reference import DataflowStructure, ReferenceSource


class FakeReference(ReferenceSource):
    """Answers with the structure and codes given, or raises with *reason*.

    Attributes:
        calls: The methods called, in order.
    """

    def __init__(
        self,
        structure: DataflowStructure | None = None,
        codes: frozenset[str] = frozenset(),
        reason: str | None = None,
    ) -> None:
        super().__init__()
        self._structure = structure
        self._codes = codes
        self._reason = reason
        self.calls: list[str] = []

    @override
    def structure(self) -> DataflowStructure:
        self.calls.append("structure")
        if self._reason is not None or self._structure is None:
            raise ReferenceUnavailableError(self._reason or "no structure")
        return self._structure

    @override
    def ref_area_codes(self) -> frozenset[str]:
        self.calls.append("ref_area_codes")
        if self._reason is not None:
            raise ReferenceUnavailableError(self._reason)
        return self._codes
