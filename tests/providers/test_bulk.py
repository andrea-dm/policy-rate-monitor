from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor.bulk import BulkFileProvider
from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.providers import observation_schema
from tests.providers.contract import ProviderContract
from tests.test_transform import HEADER, US, US_SERIES, make_config

if TYPE_CHECKING:
    from pathlib import Path

    import pyarrow as pa

pytestmark = pytest.mark.contract

CONFIG = make_config()
PAGE_URL = "https://example.org/downloads"
ZIP_URL = "https://example.org/rates.zip"
GB = "D: Daily,GB: United Kingdom"
GB_SERIES = "368: Per cent per year,0: Units,2: Two,GB policy rate"


def source_zip() -> bytes:
    rows = [
        f"{GB},2024-01-02,5.25,{GB_SERIES},A: Normal",
        f"{US},1969-12-31,6.00,{US_SERIES},A: Normal",
        f"{US},2024-01-02,NaN,{US_SERIES},M: Missing",
        f"{US},2024-01-01,5.50,{US_SERIES},A: Normal",
        f"{US},2024-01-01,5.50,{US_SERIES},A: Normal",
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("rates.csv", "\n".join([HEADER, *rows]) + "\n")
    return buffer.getvalue()


def mock_web(url: str) -> bytes:
    pages = {PAGE_URL: b'<a href="/rates.zip">rates</a>', ZIP_URL: source_zip()}
    return pages[url]


@pytest.fixture
def provider(tmp_path: Path) -> BulkFileProvider:
    provider = BulkFileProvider(CONFIG, tmp_path, get=mock_web)
    provider.fetch()
    provider.transform()
    return provider


@pytest.fixture
def schema() -> pa.Schema:
    return observation_schema(CONFIG.structure)


class TestBulkFileProvider(ProviderContract):
    pass


def test_queries_before_transform_say_which_step_to_run(tmp_path: Path) -> None:
    provider = BulkFileProvider(CONFIG, tmp_path, get=mock_web)

    assert provider.source_release() is None
    with pytest.raises(MissingInputError, match="bis-prates transform"):
        provider.observations()
