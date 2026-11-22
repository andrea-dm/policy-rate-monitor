"""The SDMX reference read from an SDMX REST API with ``pysdmx`` (the ``sdmx`` extra).

``SdmxReference`` asks the configured API with ``pysdmx``'s registry client: the
dataflow, then the data structure it points to, then the reference-area code list.
It never uses ``get_schema``, ``get_dataflow_details`` or the code lists sent with a
dataflow: some APIs answer those with cut-down code lists or with XML where JSON was
asked for. Each question is one attempt, with no retry and no cache.

Every ``pysdmx`` error, and an answer that cannot be read, becomes
``ReferenceUnavailableError`` with a plain reason. Any other exception is a bug.

See also: [Data quality](data-quality.md) and [decision
0009](adr/0009-sdmx-validation.md).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from msgspec import MsgspecError
from pydantic import ValidationError
from pysdmx.api.fmr import RegistryClient
from pysdmx.errors import NotFound, PysdmxError, Unavailable
from pysdmx.model import Codelist, DataStructureDefinition, Hierarchy, Role
from pysdmx.util import parse_maintainable_urn

from policy_rate_monitor.config.models import ArtefactRef
from policy_rate_monitor.core.errors import ReferenceUnavailableError
from policy_rate_monitor.reference import (
    ComponentRole,
    DataflowStructure,
    ReferenceSource,
    StructureComponent,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysdmx.model import Agency, Component

    from policy_rate_monitor.config.models import ReferenceSection

__all__ = ["SdmxReference"]

logger = logging.getLogger(__name__)

_ROLES: dict[Role, ComponentRole] = {
    Role.DIMENSION: "dimension",
    Role.MEASURE: "measure",
    Role.ATTRIBUTE: "attribute",
}


class SdmxReference(ReferenceSource):
    """Reads the configured SDMX reference from the configured API."""

    def __init__(self, reference: ReferenceSection) -> None:
        """Sets which API to ask and what for.

        Args:
            reference: The configuration's ``reference`` section.
        """
        super().__init__()
        self._reference = reference
        self._client = RegistryClient(
            str(reference.api_url), timeout=reference.timeout_seconds
        )

    @override
    def structure(self) -> DataflowStructure:
        """Asks for the dataflow, then for the data structure it points to.

        Raises:
            ReferenceUnavailableError: Either cannot be obtained.
        """
        dataflow = self._reference.dataflow
        structure = self._ask(
            f"dataflow {dataflow}", lambda: self._structure_of(dataflow)
        )
        return self._ask(
            f"data structure {structure}", lambda: self._components_of(structure)
        )

    @override
    def ref_area_codes(self) -> frozenset[str]:
        """Asks for the configured reference-area code list.

        Raises:
            ReferenceUnavailableError: It cannot be obtained.
        """
        code_list = self._reference.ref_area_code_list
        codes = self._ask(
            f"code list {code_list}",
            lambda: self._client.get_codes(
                code_list.agency, code_list.id, code_list.version
            ),
        )
        return frozenset(code.id for code in codes.codes)

    def _structure_of(self, dataflow: ArtefactRef) -> ArtefactRef:
        """The data structure the dataflow points to."""
        flows = self._client.get_dataflows(
            dataflow.agency, dataflow.id, dataflow.version
        )
        for flow in flows:
            if flow.id != dataflow.id:
                continue
            if isinstance(flow.structure, DataStructureDefinition):
                return _ref(flow.structure)
            if isinstance(flow.structure, str):
                urn = parse_maintainable_urn(flow.structure)
                return ArtefactRef(agency=urn.agency, id=urn.id, version=urn.version)
        msg = f"the SDMX API names no data structure for the dataflow {dataflow}"
        raise ReferenceUnavailableError(msg)

    def _components_of(self, structure: ArtefactRef) -> DataflowStructure:
        """The components of the data structure."""
        definitions = self._client.get_data_structures(
            structure.agency, structure.id, structure.version
        )
        for definition in definitions:
            if definition.id == structure.id:
                return DataflowStructure(
                    structure=structure,
                    components=tuple(
                        _component(component) for component in definition.components
                    ),
                )
        msg = f"the SDMX API has no data structure {structure}"
        raise ReferenceUnavailableError(msg)

    def _ask[T](self, what: str, call: Callable[[], T]) -> T:
        """Runs one query; turns its failure into ``ReferenceUnavailableError``."""
        try:
            return call()
        except Unavailable as error:
            reason = f"the SDMX API at {self._reference.api_url} could not be reached"
            failure: Exception = error
        except NotFound as error:
            reason = f"the SDMX API has no {what}"
            failure = error
        except PysdmxError as error:
            reason = f"the SDMX API answered the query for {what} with an error"
            reason += f" ({error.title})"
            failure = error
        except (MsgspecError, ValidationError) as error:
            reason = f"the SDMX API's answer for {what} cannot be read"
            failure = error
        logger.debug("%s: %s", reason, failure)
        raise ReferenceUnavailableError(reason) from failure


def _component(component: Component) -> StructureComponent:
    enumeration = component.enumeration
    code_list: ArtefactRef | None = None
    if isinstance(enumeration, (Codelist, Hierarchy)):
        code_list = _ref(enumeration)
    elif component.enum_ref is not None:
        urn = parse_maintainable_urn(component.enum_ref)
        code_list = ArtefactRef(agency=urn.agency, id=urn.id, version=urn.version)
    return StructureComponent(
        id=component.id, role=_ROLES[component.role], code_list=code_list
    )


def _ref(artefact: DataStructureDefinition | Codelist | Hierarchy) -> ArtefactRef:
    agency: str | Agency = artefact.agency
    return ArtefactRef(
        agency=agency if isinstance(agency, str) else agency.id,
        id=artefact.id,
        version=artefact.version,
    )
