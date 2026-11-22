from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor import bulk
from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.core.models import SourceRelease
from policy_rate_monitor.providers import observation_schema
from policy_rate_monitor.service import load_staged
from policy_rate_monitor.transform import load_series
from tests.providers.contract import ProviderContract
from tests.test_transform import CONFIG as CONFIG_DOCUMENT
from tests.test_transform import HEADER, US, US_SERIES, make_config

if TYPE_CHECKING:
    from pathlib import Path

    import pyarrow as pa

    from policy_rate_monitor.core.models import SeriesMetadata

pytestmark = pytest.mark.contract

CONFIG = make_config()
PAGE_URL = "https://example.org/downloads"
ZIP_URL = "https://example.org/rates.zip"
GB = "D: Daily,GB: United Kingdom"
GB_SERIES = "368: Per cent per year,0: Units,2: Two,GB policy rate"


ROWS = [
    f"{GB},2024-01-02,5.25,{GB_SERIES},A: Normal",
    f"{US},1969-12-31,6.00,{US_SERIES},A: Normal",
    f"{US},2024-01-02,NaN,{US_SERIES},M: Missing",
    f"{US},2024-01-01,5.50,{US_SERIES},A: Normal",
    f"{US},2024-01-01,5.50,{US_SERIES},A: Normal",
]


def source_zip(rows: list[str] = ROWS) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("rates.csv", "\n".join([HEADER, *rows]) + "\n")
    return buffer.getvalue()


def fake_web(url: str) -> bytes:
    pages = {PAGE_URL: b'<a href="/rates.zip">rates</a>', ZIP_URL: source_zip()}
    return pages[url]


@pytest.fixture
def provider(tmp_path: Path) -> BulkFileProvider:
    provider = BulkFileProvider(CONFIG, tmp_path, get=fake_web)
    provider.fetch()
    provider.transform()
    return provider


@pytest.fixture
def schema() -> pa.Schema:
    return observation_schema(CONFIG.structure)


class TestBulkFileProvider(ProviderContract):
    pass


def test_queries_before_transform_say_which_step_to_run(tmp_path: Path) -> None:
    provider = BulkFileProvider(CONFIG, tmp_path, get=fake_web)

    assert provider.source_release() is None
    for query in (provider.observations, provider.list_series):
        with pytest.raises(MissingInputError, match="does not exist") as error:
            query()
        assert error.value.step == "transform"


def test_the_release_is_the_one_staged_not_the_one_fetched_last(
    tmp_path: Path,
) -> None:
    provider = BulkFileProvider(CONFIG, tmp_path, get=fake_web)
    staged = provider.fetch()
    provider.transform()

    # A new release is fetched, but not transformed.
    newer = [*ROWS, f"{US},2024-01-03,5.75,{US_SERIES},A: Normal"]
    record = tmp_path / "raw" / "rates.json"
    old = SourceRelease.model_validate_json(record.read_text())
    expired = old.retrieved_at - timedelta(days=2)
    aged = SourceRelease.model_validate({**old.model_dump(), "retrieved_at": expired})
    record.write_text(aged.model_dump_json())
    pages = {PAGE_URL: b'<a href="/rates.zip">rates</a>', ZIP_URL: source_zip(newer)}
    fetched = BulkFileProvider(CONFIG, tmp_path, get=pages.__getitem__).fetch()
    assert fetched.checksum_sha256 != staged.checksum_sha256

    assert provider.source_release() == staged
    # What the report is built from.
    assert load_staged(provider).release == staged


def test_staged_data_of_another_configuration_says_to_transform_again(
    tmp_path: Path,
) -> None:
    BulkFileProvider(CONFIG, tmp_path, get=fake_web).fetch()
    BulkFileProvider(CONFIG, tmp_path).transform()
    # The status kept under another name: the staged table lacks that column.
    structure = json.loads(json.dumps(CONFIG_DOCUMENT["structure"]))
    structure["observation_attributes"][0]["keep_as"] = "flag"
    provider = BulkFileProvider(make_config(structure=structure), tmp_path)

    with pytest.raises(
        MissingInputError, match="was staged with another configuration"
    ) as error:
        provider.observations()
    assert error.value.step == "transform"


@pytest.mark.whitebox
def test_series_json_is_read_once_per_change_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = BulkFileProvider(CONFIG, tmp_path, get=fake_web)
    provider.fetch()
    provider.transform()
    reads: list[Path] = []

    def counted(data_dir: Path) -> list[SeriesMetadata]:
        reads.append(data_dir)
        return load_series(data_dir)

    monkeypatch.setattr(bulk, "load_series", counted)

    keys = provider.list_series()
    for key in keys * 3:
        provider.series_metadata(key)
    assert len(reads) == 1

    series_json = tmp_path / "staged" / "series.json"
    stat = series_json.stat()
    os.utime(series_json, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    assert provider.list_series() == keys
    assert len(reads) == 2  # noqa: PLR2004


def test_transform_returns_the_counts_of_the_run(tmp_path: Path) -> None:
    provider = BulkFileProvider(CONFIG, tmp_path, get=fake_web)
    provider.fetch()

    summary = provider.transform()

    # Five rows; one exact copy dropped; the 1969 row is kept.
    assert (summary.rows_read, summary.observations, summary.series) == (5, 4, 2)
    assert summary.issues == len(provider.data_quality_issues())
