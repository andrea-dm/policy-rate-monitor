from __future__ import annotations

import io
import logging
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar, override

import pytest

from policy_rate_monitor.config.models import SourceSection
from policy_rate_monitor.core.errors import (
    SourceUnavailableError,
    UnexpectedFormatError,
)
from policy_rate_monitor.fetch import (
    MAX_REDIRECTS,
    cached_zip,
    fetch,
    http_get,
    read_release,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
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


def garbled_zip(method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A compressed ZIP whose compressed data has 100 bytes zeroed."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        rows = "".join(f"{number},{number * 7}\n" for number in range(2000))
        archive.writestr("a.csv", "A,B\n" + rows)
    content = buffer.getvalue()
    return content[:100] + bytes(100) + content[200:]


def with_bad_directory_offset(content: bytes) -> bytes:
    """Overwrites the offset of the central directory in the end record."""
    return content[:-6] + b"\xff\xff\xff\xff" + content[-2:]


def with_central_field(content: bytes, offset: int, value: int) -> bytes:
    """Sets a 2-byte field of the first central directory entry (8: the flags, 10:
    the compression method)."""
    at = content.index(b"PK\x01\x02") + offset
    return content[:at] + value.to_bytes(2, "little") + content[at + 2 :]


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
        (b"<html>maintenance</html>", "is not a readable ZIP file"),
        (garbled_zip(), "is not a readable ZIP file: Error -3 while decompressing"),
        (garbled_zip(zipfile.ZIP_BZIP2), "is not a readable ZIP file: Invalid data"),
        (garbled_zip(zipfile.ZIP_LZMA), "is not a readable ZIP file: Corrupt input"),
        (with_bad_directory_offset(make_zip("a.csv")), "is not a readable ZIP file"),
        (with_central_field(make_zip("a.csv"), 8, 0x1), "is encrypted"),
        (with_central_field(make_zip("a.csv"), 10, 99), "compression method"),
        (make_zip("a.csv", "b.csv"), "should hold exactly one CSV file, found 2"),
    ],
    ids=[
        "not_a_zip",
        "garbled",
        "garbled_bzip2",
        "garbled_lzma",
        "bad_directory_offset",
        "encrypted",
        "unknown_compression",
        "two_csv",
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


@pytest.mark.contract
def test_a_temporary_failure_is_retried_after_growing_pauses(tmp_path: Path) -> None:
    source = SOURCE.model_copy(update={"max_attempts": 3})
    web = FakeWeb({PAGE_URL: PAGE})
    pauses: list[float] = []

    with pytest.raises(SourceUnavailableError, match="cannot download"):
        fetch(source, tmp_path, web, sleep=pauses.append)

    assert web.requests == [PAGE_URL, FILE_URL, FILE_URL, FILE_URL]
    assert pauses == [1, 2]


@pytest.mark.contract
def test_the_cache_lifetime_is_measured_on_the_given_clock(tmp_path: Path) -> None:
    web = FakeWeb({PAGE_URL: PAGE, FILE_URL: make_zip("data.csv")})
    start = datetime(2026, 1, 1, tzinfo=UTC)
    fetch(SOURCE, tmp_path, web, now=lambda: start)

    fetch(SOURCE, tmp_path, web, now=lambda: start + timedelta(hours=23))
    assert len(web.requests) == 2
    release = fetch(SOURCE, tmp_path, web, now=lambda: start + timedelta(hours=25))
    assert len(web.requests) == 4
    assert release.retrieved_at == start + timedelta(hours=25)


@pytest.mark.contract
@pytest.mark.parametrize("record", [b"\xff\xfe not UTF-8", b"{}"])
def test_an_unreadable_release_record_is_ignored(tmp_path: Path, record: bytes) -> None:
    web = FakeWeb({PAGE_URL: PAGE, FILE_URL: make_zip("data.csv")})
    fetch(SOURCE, tmp_path, web)
    cached_zip(SOURCE, tmp_path).with_suffix(".json").write_bytes(record)

    assert read_release(SOURCE, tmp_path) is None
    fetch(SOURCE, tmp_path, web)
    assert web.requests == [PAGE_URL, FILE_URL] * 2


# ─── Over HTTP, against a local server ────────────────────────────────────────── #


class _Handler(BaseHTTPRequestHandler):
    """Answers each path from ``routes``: (status, Location header or None, body)."""

    routes: ClassVar[dict[str, tuple[int, str | None, bytes]]] = {}
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
        self.requests.append(self.path)
        status, location, body = self.routes.get(self.path, (404, None, b""))
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class LocalWeb:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        _Handler.routes.clear()
        _Handler.requests.clear()

    def serve(
        self, path: str, status: int, body: bytes = b"", location: str | None = None
    ) -> None:
        _Handler.routes[path] = (status, location, body)

    @property
    def requests(self) -> list[str]:
        return _Handler.requests

    def source(self, max_attempts: int = 1) -> SourceSection:
        return SourceSection.model_validate(
            {
                "discovery_url": f"{self.base}/downloads",
                "file_name": "rates.zip",
                "fallback_url": f"{self.base}/fallback/rates.zip",
                "cache_ttl_hours": 24,
                "max_attempts": max_attempts,
                "connect_timeout_seconds": 5,
                "read_timeout_seconds": 5,
            }
        )


@pytest.fixture
def local_web() -> Iterator[LocalWeb]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield LocalWeb(server)
    finally:
        server.shutdown()
        server.server_close()


def get(url: str) -> bytes:
    return http_get(url, connect_timeout=5, read_timeout=5)


@pytest.mark.contract
@pytest.mark.parametrize("status", [404, 403, 410, 400])
def test_an_answer_that_is_not_temporary_fails_at_once(
    tmp_path: Path, local_web: LocalWeb, status: int
) -> None:
    local_web.serve("/downloads", 200, b'<a href="/static/rates.zip">rates</a>')
    local_web.serve("/static/rates.zip", status)
    pauses: list[float] = []

    with pytest.raises(UnexpectedFormatError, match=f"HTTP {status}"):
        fetch(local_web.source(max_attempts=3), tmp_path, sleep=pauses.append)

    assert local_web.requests == ["/downloads", "/static/rates.zip"]
    assert pauses == []


@pytest.mark.contract
@pytest.mark.parametrize("status", [503, 500, 408, 429])
def test_a_temporary_answer_is_retried_then_the_source_is_unavailable(
    tmp_path: Path, local_web: LocalWeb, status: int
) -> None:
    local_web.serve("/downloads", 200, b'<a href="/static/rates.zip">rates</a>')
    local_web.serve("/static/rates.zip", status)
    pauses: list[float] = []

    with pytest.raises(SourceUnavailableError, match=f"HTTP {status}"):
        fetch(local_web.source(max_attempts=2), tmp_path, sleep=pauses.append)

    assert local_web.requests == ["/downloads", *["/static/rates.zip"] * 2]
    assert pauses == [1]


@pytest.mark.contract
@pytest.mark.parametrize("status", [404, 503])
def test_a_failed_discovery_page_falls_back_to_the_configured_url(
    tmp_path: Path, local_web: LocalWeb, status: int
) -> None:
    local_web.serve("/downloads", status)
    local_web.serve("/fallback/rates.zip", 200, make_zip("a.csv"))

    release = fetch(local_web.source(), tmp_path)

    assert release.location == f"{local_web.base}/fallback/rates.zip"


@pytest.mark.contract
def test_up_to_five_redirects_are_followed(local_web: LocalWeb) -> None:
    for hop in range(MAX_REDIRECTS):
        local_web.serve(f"/hop{hop}", 302, location=f"/hop{hop + 1}")
    local_web.serve(f"/hop{MAX_REDIRECTS}", 200, b"body")

    assert get(f"{local_web.base}/hop0") == b"body"
    assert len(local_web.requests) == MAX_REDIRECTS + 1


@pytest.mark.contract
def test_a_sixth_redirect_is_refused(local_web: LocalWeb) -> None:
    for hop in range(MAX_REDIRECTS + 1):
        local_web.serve(f"/hop{hop}", 301, location=f"{local_web.base}/hop{hop + 1}")
    local_web.serve(f"/hop{MAX_REDIRECTS + 1}", 200, b"body")

    with pytest.raises(UnexpectedFormatError, match="more than 5 redirects"):
        get(f"{local_web.base}/hop0")
    assert len(local_web.requests) == MAX_REDIRECTS + 1


@pytest.mark.contract
def test_a_server_that_cannot_be_reached_is_unavailable() -> None:
    with ThreadingHTTPServer(("127.0.0.1", 0), _Handler) as closed:
        port = closed.server_address[1]
    # Nothing listens on the port any more.
    with pytest.raises(SourceUnavailableError, match="cannot download"):
        get(f"http://127.0.0.1:{port}/rates.zip")
