"""Data providers: the one way the rest of the package reads data.

Depends only on ``core`` and ``config``.
"""

from policy_rate_monitor.providers.base import DataProvider
from policy_rate_monitor.providers.memory import InMemoryProvider
from policy_rate_monitor.providers.schema import observation_schema, select_series

__all__ = ["DataProvider", "InMemoryProvider", "observation_schema", "select_series"]
