"""Writing a set of files so that a failure never leaves a mix of old and new ones.

``transform`` writes the staged layer and ``report`` the output files with
``write_files``: every file is first written beside its target under a temporary name,
and only once all of them are written are they renamed into place, in the order given.
Each file is replaced whole, and a full disk or a crash while writing leaves the
previous files as they were.

The renames are not one step: a reader in another process that reads the set while
they run can pair files of two runs (decision 0017).

See also: [decisions 0012](adr/0012-elt-raw-and-staged-layers.md) and
[0017](adr/0017-bad-input-failures-and-writes.md).

Examples:
    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     target = Path(folder) / "out" / "a.txt"
    ...     write_files({target: b"new"})
    ...     print(target.read_text(), sorted(p.name for p in target.parent.iterdir()))
    new ['a.txt']
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["write_files"]


def write_files(files: Mapping[Path, bytes]) -> None:
    """Writes every file, then renames them all into place, in the order given.

    The folders are created when absent. Put last the file that tells a reader the set
    is complete (``staged/release.json``).

    Args:
        files: The content of each file, by path.

    Raises:
        OSError: A file cannot be written; no target has been replaced, and the
            temporary files are removed.
    """
    written: dict[Path, Path] = {}
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            written[path] = path.with_name(f".{path.name}.tmp")
            written[path].write_bytes(content)
    except BaseException:
        for temporary in written.values():
            temporary.unlink(missing_ok=True)
        raise
    # TODO(andrea-dm): a reader in another process can pair files of two runs while
    # these renames run; versioned folders behind one pointer file would close it.
    # https://github.com/andrea-dm/policy-rate-monitor/issues/35
    for path, temporary in written.items():
        os.replace(temporary, path)
