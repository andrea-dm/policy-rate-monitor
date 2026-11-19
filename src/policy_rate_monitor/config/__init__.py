"""Validated configuration contracts. Depends only on ``core``.

``schema`` is not imported here: ``python -m policy_rate_monitor.config.schema`` would
find the module already imported and warn.
"""

from __future__ import annotations

from policy_rate_monitor.config.models import (
    CANONICAL_COLUMNS,
    ColumnsSection,
    Concept,
    DatasetConfig,
    DatasetSection,
    Dimension,
    Loc,
    Measure,
    MetadataConcepts,
    MetadataField,
    ObservationAttribute,
    OutputSection,
    Role,
    RuleSpec,
    SnapshotSection,
    SourceSection,
    StructureSection,
    TimeDimension,
)
from policy_rate_monitor.config.parsing import parse_config
from policy_rate_monitor.config.source import ConfigSource, FileConfigSource

__all__ = [
    "CANONICAL_COLUMNS",
    "ColumnsSection",
    "Concept",
    "ConfigSource",
    "DatasetConfig",
    "DatasetSection",
    "Dimension",
    "FileConfigSource",
    "Loc",
    "Measure",
    "MetadataConcepts",
    "MetadataField",
    "ObservationAttribute",
    "OutputSection",
    "Role",
    "RuleSpec",
    "SnapshotSection",
    "SourceSection",
    "StructureSection",
    "TimeDimension",
    "parse_config",
]
