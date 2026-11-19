"""Tests of the ``ConfigSource`` interface and the file source."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from policy_rate_monitor.config import ConfigSource, FileConfigSource
from policy_rate_monitor.core.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.contract
def test_missing_file_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    with pytest.raises(ConfigError) as caught:
        FileConfigSource(path).load()

    assert str(caught.value) == (
        f"{path}: cannot read the configuration: No such file or directory"
    )
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.unit
def test_source_without_load_cannot_be_instantiated() -> None:
    class Incomplete(ConfigSource):
        pass

    # Through type[...], so the type checker does not reject the call the test makes.
    incomplete = cast(type[ConfigSource], Incomplete)

    with pytest.raises(TypeError, match="abstract"):
        incomplete()
