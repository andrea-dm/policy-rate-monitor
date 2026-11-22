"""Validated configuration contracts. Depends only on ``core``.

See also: [Configuration](configuration.md).
"""

from __future__ import annotations

from policy_rate_monitor.config.models import (
    CANONICAL_COLUMNS,
    CODE_PATTERN,
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
    requested_codes,
)
from policy_rate_monitor.config.parsing import parse_config
from policy_rate_monitor.config.source import ConfigSource, FileConfigSource

__all__ = [
    "CANONICAL_COLUMNS",
    "CODE_PATTERN",
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
    "requested_codes",
]
