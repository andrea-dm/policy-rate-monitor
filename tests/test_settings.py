from __future__ import annotations

from pathlib import Path

import pytest

from policy_rate_monitor.config import FileConfigSource
from policy_rate_monitor.settings import (
    CONFIG_VARIABLE,
    DATA_DIR_VARIABLE,
    DEFAULT_CONFIG,
    Origin,
    Setting,
    config_path,
    data_dir,
)

CONFIG = Path(__file__).parents[1] / "configs" / "bis_cbpol.json"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("flag", "environ", "expected"),
    [
        (Path("flag.json"), {CONFIG_VARIABLE: "env.json"}, ("flag.json", Origin.FLAG)),
        (None, {CONFIG_VARIABLE: "env.json"}, ("env.json", Origin.ENVIRONMENT)),
        (None, {CONFIG_VARIABLE: ""}, (str(DEFAULT_CONFIG), Origin.DEFAULT)),
        (None, {}, (str(DEFAULT_CONFIG), Origin.DEFAULT)),
    ],
    ids=["flag-wins", "variable-wins", "empty-variable-is-unset", "default"],
)
def test_config_path_takes_the_first_layer_that_gives_one(
    flag: Path | None, environ: dict[str, str], expected: tuple[str, Origin]
) -> None:
    path, origin = expected

    assert config_path(flag, environ) == Setting(Path(path), origin)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("flag", "environ", "expected"),
    [
        (Path("flag"), {DATA_DIR_VARIABLE: "env"}, ("flag", Origin.FLAG)),
        (None, {DATA_DIR_VARIABLE: "env"}, ("env", Origin.ENVIRONMENT)),
        (None, {DATA_DIR_VARIABLE: ""}, ("data", Origin.CONFIGURATION)),
        (None, {}, ("data", Origin.CONFIGURATION)),
    ],
    ids=["flag-wins", "variable-wins", "empty-variable-is-unset", "configuration"],
)
def test_data_dir_takes_the_first_layer_that_gives_one(
    flag: Path | None, environ: dict[str, str], expected: tuple[str, Origin]
) -> None:
    config = FileConfigSource(CONFIG).load()
    path, origin = expected

    assert data_dir(flag, environ, config) == Setting(Path(path), origin)
