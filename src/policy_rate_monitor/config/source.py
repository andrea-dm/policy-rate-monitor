"""Where a configuration comes from: the ``ConfigSource`` interface and a file source.

A source reads a document and passes it to ``parse_config``, so every source validates
the same way and gives the same messages.

See also: [Configuration](configuration.md).

Examples:
    Creating a file source does no I/O; ``load`` reads the file:

    >>> FileConfigSource("configs/example.json")
    FileConfigSource('configs/example.json')
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, override

from policy_rate_monitor.config.parsing import parse_config
from policy_rate_monitor.core.errors import ConfigError

if TYPE_CHECKING:
    import os

    from policy_rate_monitor.config.models import DatasetConfig

__all__ = ["ConfigSource", "FileConfigSource"]


class ConfigSource(ABC):
    """Supplies a validated dataset configuration: a file today, a service later."""

    @abstractmethod
    def load(self) -> DatasetConfig:
        """Returns the validated configuration.

        Returns:
            The configuration, checked against the models.

        Raises:
            ConfigError: The configuration cannot be read or does not validate.
        """


class FileConfigSource(ConfigSource):
    """Reads the configuration from a UTF-8 JSON file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        """Stores the path; nothing is read until ``load``.

        Args:
            path: The JSON file. A relative path is relative to the working directory.
        """
        super().__init__()
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """The configuration file, as given."""
        return self._path

    @override
    def load(self) -> DatasetConfig:
        """Reads and validates the file.

        Returns:
            The validated configuration.

        Raises:
            ConfigError: The file cannot be read, is not UTF-8 JSON, or does not
                validate. The message starts with the path; the original exception
                is the ``__cause__``.
        """
        try:
            # TODO(andrea-dm): read at most about 1 MiB; report a larger file.
            # https://github.com/andrea-dm/policy-rate-monitor/issues/8
            document = self._path.read_bytes()
        except (OSError, ValueError) as error:
            # ValueError: the path holds a NUL byte, which no file system accepts.
            reason = error.strerror if isinstance(error, OSError) else None
            reason = reason or str(error)
            msg = f"{self._path}: cannot read the configuration: {reason}"
            raise ConfigError(msg) from error
        return parse_config(document, origin=str(self._path))

    @override
    def __repr__(self) -> str:
        """Shows the path, e.g. ``FileConfigSource('configs/example.json')``."""
        return f"{type(self).__name__}({str(self._path)!r})"
