"""A small source file shaped like BIS's, for tests that run the committed config."""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

REF_AREAS = {
    "US": "United States",
    "GB": "United Kingdom",
    "JP": "Japan",
    "CH": "Switzerland",
    "XM": "Euro area",
}
HEADER = [
    "FREQ",
    "REF_AREA",
    "TIME_PERIOD",
    "OBS_VALUE",
    "OBS_STATUS",
    "OBS_CONF",
    "OBS_PRE_BREAK",
    "UNIT_MEASURE",
    "UNIT_MULT",
    "DECIMALS",
    "TITLE",
]


def write_bis_zip(data_dir: Path, file_name: str) -> None:
    """Writes ``raw/<file_name>``: one daily row per country or area above."""
    rows = [
        f"D: Daily,{code}: {name},2026-01-02,2.5,A: Normal value,F: Free,,"
        f"368: Per cent per year,0: Units,2: Two,{name} policy rate"
        for code, name in REF_AREAS.items()
    ]
    raw = data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw / file_name, "w") as archive:
        archive.writestr("rates.csv", "\n".join([",".join(HEADER), *rows]) + "\n")
