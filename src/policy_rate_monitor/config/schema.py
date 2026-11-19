"""The configuration's JSON Schema, in the one text form that is committed.

``render_json_schema`` builds the schema from the models, so the committed
``configs/config.schema.json`` documents exactly what loads. A test compares the two.
Until the CLI has its own command, rewrite the committed file with::

    uv run python -m policy_rate_monitor.config.schema configs/config.schema.json

Examples:
    >>> render_json_schema().splitlines()[1].strip()
    '"$schema": "https://json-schema.org/draft/2020-12/schema",'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from policy_rate_monitor.config.models import DatasetConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["JSON_SCHEMA_DIALECT", "REGENERATE_COMMAND", "render_json_schema"]

# TODO(andrea-dm): take the URI from pydantic's GenerateJsonSchema.schema_dialect.
# https://github.com/andrea-dm/policy-rate-monitor/issues/8
JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
"""The JSON Schema version pydantic generates."""

REGENERATE_COMMAND: Final = (
    "uv run python -m policy_rate_monitor.config.schema configs/config.schema.json"
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


def _main(argv: Sequence[str]) -> int:
    """Writes the schema to the one path given.

    Args:
        argv: The command-line arguments after the program name.

    Returns:
        0 when the file is written; 2, with the usage on stderr, otherwise.
    """
    if len(argv) != 1:
        sys.stderr.write("usage: python -m policy_rate_monitor.config.schema PATH\n")
        return 2
    # TODO(andrea-dm): report OS errors without a traceback; refuse under -OO.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/8
    Path(argv[0]).write_text(render_json_schema(), encoding="ascii", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
