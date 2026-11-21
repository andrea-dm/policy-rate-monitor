"""The HTTP API (needs the ``web`` extra): ``create_app`` and ``serve``.

Nothing in the base layers imports this package; ``bis-prates serve`` imports it only
when it runs.

See also: [HTTP API](api.md).
"""

from __future__ import annotations

from policy_rate_monitor.web.app import create_app, serve

__all__ = ["create_app", "serve"]
