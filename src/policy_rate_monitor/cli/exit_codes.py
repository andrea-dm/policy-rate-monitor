"""Exit codes of ``bis-prates`` and the mapping from the package's errors to them.

See also: [CLI reference: exit codes](cli.md#exit-codes) and [decision
0010](adr/0010-cli-and-exit-codes.md).

Examples:
    A subclass gets the code of its nearest mapped parent; anything else is a bug:

    >>> exit_code_for(MissingInputError("no cached ZIP"))
    <ExitCode.MISSING_INPUT: 5>
    >>> exit_code_for(KeyError("x"))
    <ExitCode.INTERNAL_ERROR: 3>
"""

from __future__ import annotations

from enum import IntEnum

from policy_rate_monitor.core.errors import (
    ConfigError,
    MissingInputError,
    SourceUnavailableError,
    UnexpectedFormatError,
)

__all__ = ["ExitCode", "exit_code_for"]


class ExitCode(IntEnum):
    """What the process returns to the shell."""

    OK = 0
    NO_RESULT = 1
    USAGE_ERROR = 2
    INTERNAL_ERROR = 3
    CONFIG_ERROR = 4
    MISSING_INPUT = 5
    SOURCE_UNAVAILABLE = 6
    UNEXPECTED_FORMAT = 7
    OUTPUT_ERROR = 8


_CODES: dict[type[BaseException], ExitCode] = {
    ConfigError: ExitCode.CONFIG_ERROR,
    MissingInputError: ExitCode.MISSING_INPUT,
    SourceUnavailableError: ExitCode.SOURCE_UNAVAILABLE,
    UnexpectedFormatError: ExitCode.UNEXPECTED_FORMAT,
}


def exit_code_for(error: BaseException) -> ExitCode:
    """Returns the exit code for *error*, looking through its parent classes.

    Args:
        error: The exception that stopped the command.

    Returns:
        The code of the first class in the error's hierarchy that has one, else
        ``INTERNAL_ERROR``.
    """
    for cls in type(error).__mro__:
        if cls in _CODES:
            return _CODES[cls]
    return ExitCode.INTERNAL_ERROR
