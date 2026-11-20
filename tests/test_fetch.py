from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from policy_rate_monitor.config.models import SourceSection
from policy_rate_monitor.core.errors import (
    SourceUnavailableError,
    UnexpectedFormatError,
)
from policy_rate_monitor.fetch import cached_zip, fetch, read_release

if TYPE_CHECKING:
    from pathlib import Path

PAGE_URL = "https://example.org/downloads"
FILE_URL = "https://example.org/static/rates.zip"
FALLBACK_URL = "https://example.org/fallback/rates.zip"

SOURCE = SourceSection.model_validate(
    {
        "discovery_url": PAGE_URL,
        "file_name": "rates.zip",
        "fallback_url": FALLBACK_URL,
        "cache_ttl_hours": 24,
        "max_attempts": 1,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 1,
    }
)
PAGE = (
    b'<html><a href="/static/other.zip">x</a><a href="/static/rates.zip">y</a></html>'
)


def make_zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "A,B\n1,2\n")
    return buffer.getvalue()


class FakeWeb:
    """Serves fixed bodies by URL and records every request; a missing URL is down."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requests.append(url)
        if url not in self.pages:
            msg = f"cannot download {url}"
            raise SourceUnavailableError(msg)
        return self.pages[url]


def age_cache(data_dir: Path, hours: int) -> None:
    release = read_release(SOURCE, data_dir)
    assert release is not None
    old = release.model_copy(
        update={"retrieved_at": datetime.now(UTC) - timedelta(hours=hours)}
    )
    meta = cached_zip(SOURCE, data_dir).with_suffix(".json")
    meta.write_text(old.model_dump_json(), encoding="utf-8")


@pytest.mark.contract
def test_second_fetch_within_the_cache_lifetime_makes_no_request(
    tmp_path: Path,
) -> None:
    web = FakeWeb({PAGE_URL: PAGE, FILE_URL: make_zip("data.csv")})

    first = fetch(SOURCE, tmp_path, web)
    second = fetch(SOURCE, tmp_path, web)

    assert web.requests == [PAGE_URL, FILE_URL]
    assert second == first
    assert first.location == FILE_URL
    assert cached_zip(SOURCE, tmp_path).read_bytes() == make_zip("data.csv")


@pytest.mark.contract
def test_fallback_url_is_used_when_the_page_has_no_link(tmp_path: Path) -> None:
    web = FakeWeb({PAGE_URL: b"<html>no links</html>", FALLBACK_URL: make_zip("a.csv")})

    release = fetch(SOURCE, tmp_path, web)

    assert release.location == FALLBACK_URL


@pytest.mark.contract
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>maintenance</html>", "is not a ZIP file"),
        (make_zip("a.csv", "b.csv"), "should hold exactly one CSV file, found 2"),
    ],
)
def test_unexpected_download_fails_and_keeps_the_cached_copy(
    tmp_path: Path, body: bytes, message: str
) -> None:
    good = make_zip("data.csv")
    fetch(SOURCE, tmp_path, FakeWeb({PAGE_URL: PAGE, FILE_URL: good}))
    age_cache(tmp_path, hours=48)

    with pytest.raises(UnexpectedFormatError, match=message):
        fetch(SOURCE, tmp_path, FakeWeb({PAGE_URL: PAGE, FILE_URL: body}))

    assert cached_zip(SOURCE, tmp_path).read_bytes() == good


@pytest.mark.contract
def test_network_down_uses_an_old_cached_copy_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first = fetch(
        SOURCE, tmp_path, FakeWeb({PAGE_URL: PAGE, FILE_URL: make_zip("a.csv")})
    )
    age_cache(tmp_path, hours=48)

    with caplog.at_level(logging.WARNING):
        release = fetch(SOURCE, tmp_path, FakeWeb({}))

    assert release.checksum_sha256 == first.checksum_sha256
    assert "using the cached copy" in caplog.text


@pytest.mark.contract
def test_network_down_without_a_cached_copy_fails(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError, match="cannot download"):
        fetch(SOURCE, tmp_path, FakeWeb({}))
