"""Where the configuration file and the data directory come from.

Each one is taken from the first place that gives it: the command line, an
environment variable, then the configuration or a fixed default. The CLI and the
HTTP API both resolve them here, so they read the same files; a service has no
command line and is set up through its environment.

>>> setting = config_path(None, {"BIS_PRATES_CONFIG": "other.json"})
>>> str(setting.value), str(setting.origin)
('other.json', 'environment')

See also: [CLI reference](cli.md#where-the-options-come-from).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from policy_rate_monitor.config import DatasetConfig

__all__ = [
    "CONFIG_VARIABLE",
    "DATA_DIR_VARIABLE",
    "DEFAULT_CONFIG",
    "Origin",
    "Setting",
    "config_path",
    "data_dir",
]

CONFIG_VARIABLE: Final = "BIS_PRATES_CONFIG"
"""The environment variable that names the configuration file."""

DATA_DIR_VARIABLE: Final = "BIS_PRATES_DATA_DIR"
"""The environment variable that names the data directory."""

DEFAULT_CONFIG: Final = Path("configs/bis_cbpol.json")
"""The configuration used when nothing else names one."""


class Origin(StrEnum):
    """Where a setting's value came from."""

    FLAG = "flag"
    """The command line."""
    ENVIRONMENT = "environment"
    """An environment variable."""
    CONFIGURATION = "configuration"
    """The configuration file."""
    DEFAULT = "default"
    """The built-in default."""


@dataclass(frozen=True)
class Setting:
    """A resolved path and where it came from."""

    value: Path
    """The path, as given; a relative path is relative to the current folder."""
    origin: Origin
    """Where the path came from."""


def config_path(flag: Path | None, environ: Mapping[str, str]) -> Setting:
    """Resolves the configuration file.

    The order is the flag, then ``BIS_PRATES_CONFIG``, then ``DEFAULT_CONFIG``. The
    configuration cannot name itself, so there is no configuration layer.

    Args:
        flag: The path given on the command line, or ``None``.
        environ: The environment, usually ``os.environ``.

    Returns:
        The path and where it came from.
    """
    return _resolve(flag, environ, CONFIG_VARIABLE, DEFAULT_CONFIG, Origin.DEFAULT)


def data_dir(
    flag: Path | None, environ: Mapping[str, str], config: DatasetConfig
) -> Setting:
    """Resolves the data directory.

    The order is the flag, then ``BIS_PRATES_DATA_DIR``, then ``dataset.data_dir``
    in the configuration.

    Args:
        flag: The path given on the command line, or ``None``.
        environ: The environment, usually ``os.environ``.
        config: The loaded configuration.

    Returns:
        The path and where it came from.
    """
    fallback = config.dataset.data_dir
    return _resolve(flag, environ, DATA_DIR_VARIABLE, fallback, Origin.CONFIGURATION)


def _resolve(
    flag: Path | None,
    environ: Mapping[str, str],
    variable: str,
    fallback: Path,
    fallback_origin: Origin,
) -> Setting:
    if flag is not None:
        return Setting(flag, Origin.FLAG)
    # An empty variable counts as unset, as `export BIS_PRATES_CONFIG=` is
    # the usual way to clear one in a shell script.
    text = environ.get(variable, "")
    if text:
        return Setting(Path(text), Origin.ENVIRONMENT)
    return Setting(fallback, fallback_origin)
