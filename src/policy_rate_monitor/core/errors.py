"""Typed errors raised on purpose by the package.

The errors describe *what* went wrong in domain terms and carry no exit codes or HTTP
statuses: the CLI (``cli.exit_codes``) and the API map them, so ``core`` imports nothing
from the layers above it. Anything that is not a ``PolicyRateMonitorError`` is a bug.

See also: [CLI reference: exit codes](cli.md#exit-codes).

Examples:
    Callers catch the base class to tell an error raised on purpose from a bug:

    >>> try:
    ...     raise MissingInputError("data/raw/rates.zip does not exist", step="fetch")
    ... except PolicyRateMonitorError as error:
    ...     print(type(error).__name__, "-", error, "- run", error.step)
    MissingInputError - data/raw/rates.zip does not exist - run fetch
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
    """The input of a step is absent, e.g. ``transform`` before ``fetch``.

    The message says what is missing; ``step`` names the step that makes it, and each
    adapter words the hint its own way: ``bis-prates transform`` on the command line,
    ``POST /jobs/transform`` over HTTP.

    Attributes:
        step: The step to run first, or ``None``.
    """

    def __init__(self, message: str, *, step: str | None = None) -> None:
        """Records what is missing and the step that makes it.

        Args:
            message: What is missing, e.g. the path of the absent file.
            step: The step to run first (``fetch``, ``transform``), or ``None`` when
                no step makes the input.
        """
        super().__init__(message)
        self.step = step
