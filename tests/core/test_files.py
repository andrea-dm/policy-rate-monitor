"""Contract tests of writing a set of files."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor.core.files import write_files

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.contract


def test_the_files_replace_the_old_ones_and_no_temporary_file_is_left(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_bytes(b"old a")

    write_files({tmp_path / "a.txt": b"new a", tmp_path / "sub" / "b.txt": b"new b"})

    assert (tmp_path / "a.txt").read_bytes() == b"new a"
    assert (tmp_path / "sub" / "b.txt").read_bytes() == b"new b"
    assert sorted(path.name for path in tmp_path.rglob("*")) == [
        "a.txt",
        "b.txt",
        "sub",
    ]


def test_a_failed_write_replaces_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"old a")
    (tmp_path / "taken").write_bytes(b"a file where a folder is needed")

    with pytest.raises(OSError, match="taken"):
        write_files({tmp_path / "a.txt": b"new a", tmp_path / "taken" / "b.txt": b"b"})

    assert (tmp_path / "a.txt").read_bytes() == b"old a"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.txt", "taken"]
