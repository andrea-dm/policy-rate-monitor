"""Data providers: the one way the rest of the package reads data.

Depends only on ``core`` and ``config``.

See also: [decision 0008](adr/0008-provider-interface.md) and
[Architecture](architecture.md).
"""

from policy_rate_monitor.providers.base import DataProvider
from policy_rate_monitor.providers.memory import InMemoryProvider
from policy_rate_monitor.providers.schema import observation_schema, select_series

__all__ = ["DataProvider", "InMemoryProvider", "observation_schema", "select_series"]
