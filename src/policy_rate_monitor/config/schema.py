"""The configuration's JSON Schema, in the one text form that is committed.

``render_json_schema`` builds the schema from the models, so the committed
``configs/config.schema.json`` documents exactly what loads. A test compares the two.
Rewrite the committed file with::

    uv run bis-prates config schema > configs/config.schema.json

Examples:
    >>> render_json_schema().splitlines()[1].strip()
    '"$schema": "https://json-schema.org/draft/2020-12/schema",'
"""

from __future__ import annotations

import json
from typing import Final

from policy_rate_monitor.config.models import DatasetConfig

__all__ = ["JSON_SCHEMA_DIALECT", "REGENERATE_COMMAND", "render_json_schema"]

# TODO(andrea-dm): take the URI from pydantic's GenerateJsonSchema.schema_dialect.
# https://github.com/andrea-dm/policy-rate-monitor/issues/8
JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
"""The JSON Schema version pydantic generates."""

REGENERATE_COMMAND: Final = (
    "uv run bis-prates config schema > configs/config.schema.json"
)
"""The command that rewrites the committed schema."""


def render_json_schema() -> str:
    """Returns the configuration's JSON Schema in its one committed text form.

    The form: the dialect key first, then pydantic's own key order, 2-space indent,
    non-ASCII characters escaped, and one trailing newline.

    Returns:
        The schema text.
    """
    schema = DatasetConfig.model_json_schema(by_alias=True, mode="validation")
    document = {"$schema": JSON_SCHEMA_DIALECT, **schema}
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"
