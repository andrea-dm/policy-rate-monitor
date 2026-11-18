"""Typed errors raised on purpose by the package.

The errors describe *what* went wrong in domain terms and carry no exit codes or HTTP
statuses: the CLI (``cli.exit_codes``) and the API map them, so ``core`` imports nothing
from the layers above it. Anything that is not a ``PolicyRateMonitorError`` is a bug.

Examples:
    Callers catch the base class to tell an error raised on purpose from a bug:

    >>> try:
    ...     raise MissingInputError("no cached ZIP: run `bis-prates fetch` first")
    ... except PolicyRateMonitorError as error:
    ...     print(type(error).__name__, "-", error)
    MissingInputError - no cached ZIP: run `bis-prates fetch` first
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "MissingInputError",
    "PolicyRateMonitorError",
    "SourceUnavailableError",
    "UnexpectedFormatError",
]


class PolicyRateMonitorError(Exception):
    """Base class of every error the package raises on purpose."""


class ConfigError(PolicyRateMonitorError):
    """The configuration is missing, unreadable, or fails validation."""


class SourceUnavailableError(PolicyRateMonitorError):
    """The data source cannot be reached and nothing usable is cached.

    The only transient error: retrying later may succeed.
    """


class UnexpectedFormatError(PolicyRateMonitorError):
    """The source data is not shaped as the configuration describes.

    Retrying does not help: the source or the configuration has to change.
    """


class MissingInputError(PolicyRateMonitorError):
    """The input of a step is absent, e.g. ``transform`` before ``fetch``."""
