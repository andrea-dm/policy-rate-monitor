"""The SDMX reference that the data is checked against, read through an interface.

A ``ReferenceSource`` answers two questions about the configured dataflow: which
components its structure has, and which codes, with their names, the configured
reference-area code list holds. The ``sdmx`` module answers them from an SDMX REST
API (it needs the ``sdmx`` extra); tests use a fake. When a source cannot answer, it
raises ``ReferenceUnavailableError``: ``transform`` then reports each SDMX rule as
"not evaluated", and ``service.summary`` leaves the requested codes unchecked.

A source keeps nothing between calls: every run of ``transform`` asks again, so a
long-running ``serve`` never checks against an old reference.

See also: [Data quality](data-quality.md) and [decision
0009](adr/0009-sdmx-validation.md).

Examples:
    With no source given, every question is answered with the reason:

    >>> try:
    ...     NO_REFERENCE.ref_area_names()
    ... except ReferenceUnavailableError as error:
    ...     print(error.reason)
    no SDMX reference source was given
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, override

from policy_rate_monitor.core.errors import ReferenceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from policy_rate_monitor.config.models import ArtefactRef

__all__ = [
    "NO_REFERENCE",
    "ComponentRole",
    "DataflowStructure",
    "ReferenceSource",
    "StructureComponent",
    "UnavailableReference",
]

ComponentRole = Literal["dimension", "measure", "attribute"]
"""The role of a component in an SDMX data structure. The time dimension is not one
of them: it is not read (see ``DataflowStructure``)."""


@dataclass(frozen=True, slots=True)
class StructureComponent:
    """One component of the data structure.

    Attributes:
        id: The component id, as in the column headers.
        role: Dimension, measure or attribute.
        code_list: The code list of a coded component; ``None`` if it is not coded.
    """

    id: str
    role: ComponentRole
    code_list: ArtefactRef | None

    @property
    def coded(self) -> bool:
        """Whether the component's values are codes of a code list."""
        return self.code_list is not None


@dataclass(frozen=True, slots=True)
class DataflowStructure:
    """The data structure that the configured dataflow points to.

    Attributes:
        structure: The data structure itself.
        components: Its dimensions, measures and attributes, without the time
            dimension, which ``pysdmx`` does not read from every API.
    """

    structure: ArtefactRef
    components: tuple[StructureComponent, ...]


class ReferenceSource(ABC):
    """Answers questions about the configured SDMX reference, one request at a time."""

    @abstractmethod
    def structure(self) -> DataflowStructure:
        """Returns the structure the configured dataflow points to.

        Returns:
            The structure and its components.

        Raises:
            ReferenceUnavailableError: The structure cannot be obtained.
        """

    @abstractmethod
    def ref_area_names(self) -> Mapping[str, str]:
        """Returns the codes of the configured reference-area code list, with names.

        Returns:
            Each code, as the code list publishes it, with its official name.

        Raises:
            ReferenceUnavailableError: The code list cannot be obtained.
        """


class UnavailableReference(ReferenceSource):
    """A source that has no reference: every question raises with the same reason."""

    def __init__(self, reason: str) -> None:
        """Sets the reason every question is answered with.

        Args:
            reason: Why there is no reference, e.g. a missing extra.
        """
        super().__init__()
        self._reason = reason

    @override
    def structure(self) -> DataflowStructure:
        """Raises ``ReferenceUnavailableError`` with the reason."""
        raise ReferenceUnavailableError(self._reason)

    @override
    def ref_area_names(self) -> Mapping[str, str]:
        """Raises ``ReferenceUnavailableError`` with the reason."""
        raise ReferenceUnavailableError(self._reason)


NO_REFERENCE: Final = UnavailableReference("no SDMX reference source was given")
"""The default source: none. It holds no state, so one shared object is safe."""
