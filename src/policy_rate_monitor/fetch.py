"""Download the source ZIP and keep a cached copy of it.

``fetch`` finds the file's link on the configured discovery page, downloads it, checks
that it is a ZIP holding exactly one CSV, and stores it in ``<data_dir>/raw/`` next to a
small JSON file that records where it came from (a ``SourceRelease``). A copy younger
than the cache lifetime is used without any request; when the network fails, an older
copy is used with a warning.

A connection error, a timeout, a 5xx, 408 or 429 answer is temporary: the download is
tried again, up to ``max_attempts`` times, then fails with ``SourceUnavailableError``.
Any other answer but 200 means the source has changed (``UnexpectedFormatError``, no
retry). Up to five redirects are followed.

See also: [How it works](how-it-works.md), sections 1 and 2,
[Data model](data-model.md) and [Upstreams](advanced/operations/upstreams.md).
"""

from __future__ import annotations

import hashlib
import http
import http.client
import logging
import lzma
import time
import zipfile
import zlib
from datetime import UTC, datetime, timedelta
from functools import partial
from html.parser import HTMLParser
from io import BytesIO
from typing import TYPE_CHECKING, override
from urllib.parse import urljoin, urlsplit

from pydantic import ValidationError

from policy_rate_monitor.core.errors import (
    SourceUnavailableError,
    UnexpectedFormatError,
)
from policy_rate_monitor.core.files import write_files
from policy_rate_monitor.core.models import SourceRelease

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from policy_rate_monitor.config.models import SourceSection

__all__ = ["cached_zip", "fetch", "http_get", "read_release", "read_zip_csv"]

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
"""Redirects ``http_get`` follows before it gives up."""

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_TEMPORARY = frozenset({408, 429})
"""Answers below 500 that are worth trying again; every 5xx is too."""


def _now() -> datetime:
    return datetime.now(UTC)


def cached_zip(source: SourceSection, data_dir: Path) -> Path:
    """Returns where the cached ZIP lives: ``<data_dir>/raw/<file_name>``.

    Args:
        source: The ``source`` section of the configuration.
        data_dir: The data directory.

    Returns:
        The path of the ZIP, whether it exists or not.
    """
    return data_dir / "raw" / source.file_name


def read_release(source: SourceSection, data_dir: Path) -> SourceRelease | None:
    """Returns the release of the cached ZIP, or ``None`` if none is usable.

    Args:
        source: The ``source`` section of the configuration.
        data_dir: The data directory.

    Returns:
        The release recorded next to the ZIP; ``None`` when the ZIP or its record is
        missing, or the record cannot be read (not UTF-8, or not a valid record).
    """
    zip_path = cached_zip(source, data_dir)
    meta_path = zip_path.with_suffix(".json")
    if not zip_path.is_file() or not meta_path.is_file():
        return None
    try:
        return SourceRelease.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except (ValidationError, UnicodeDecodeError):
        logger.warning("%s is unreadable; the cached copy is ignored", meta_path)
        return None


def fetch(
    source: SourceSection,
    data_dir: Path,
    get: Callable[[str], bytes] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _now,
) -> SourceRelease:
    """Makes sure a recent copy of the source ZIP is cached, and returns its release.

    Args:
        source: The ``source`` section of the configuration.
        data_dir: The data directory; the ZIP goes to its ``raw/`` folder.
        get: Downloads one URL and returns the body. Defaults to ``http_get`` with the
            configured timeouts; tests pass a stand-in.
        sleep: Waits the given seconds between two attempts; tests pass a stand-in.
        now: Returns the current time, timezone-aware; tests pass a stand-in.

    Returns:
        The release of the cached copy: a fresh download, or the cached one when it is
        younger than the cache lifetime or the network is down.

    Raises:
        SourceUnavailableError: Nothing can be downloaded (after ``max_attempts``
            attempts) and nothing is cached.
        UnexpectedFormatError: The server answers with a status that is not
            temporary, or the download is not a ZIP holding exactly one CSV. The
            cached copy, if any, is kept.
    """
    download = get or partial(
        http_get,
        connect_timeout=source.connect_timeout_seconds,
        read_timeout=source.read_timeout_seconds,
    )
    cached = read_release(source, data_dir)
    if cached is not None:
        age = now() - cached.retrieved_at
        if age < timedelta(hours=source.cache_ttl_hours):
            logger.info("using the cached copy from %s", cached.retrieved_at)
            return cached

    try:
        url = _find_download_url(source, download)
        content = _download(url, source.max_attempts, download, sleep)
    except SourceUnavailableError as error:
        if cached is None:
            raise
        logger.warning("%s; using the cached copy from %s", error, cached.retrieved_at)
        return cached

    read_zip_csv(content, url)
    release = SourceRelease(
        location=url,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at=now(),
    )
    _store(cached_zip(source, data_dir), content, release)
    logger.info("downloaded %s (%d bytes)", url, len(content))
    return release


def http_get(url: str, connect_timeout: float, read_timeout: float) -> bytes:
    """Downloads one URL over HTTP(S), with separate connect and read timeouts.

    Follows up to ``MAX_REDIRECTS`` redirects.

    Args:
        url: The address to download.
        connect_timeout: Seconds to wait for the connection.
        read_timeout: Seconds to wait for each read once connected.

    Returns:
        The body of the response.

    Raises:
        SourceUnavailableError: The server cannot be reached, does not answer in
            time, or answers with a temporary error (5xx, 408, 429).
        UnexpectedFormatError: The server answers with any other status but 200, or
            redirects more than ``MAX_REDIRECTS`` times.
    """
    location = url
    for _ in range(MAX_REDIRECTS + 1):
        status, reason, body, redirect = _get_once(
            location, connect_timeout, read_timeout
        )
        if status == http.HTTPStatus.OK:
            return body
        if status in _REDIRECTS and redirect:
            location = urljoin(location, redirect)
            continue
        msg = f"cannot download {location}: HTTP {status} {reason}"
        if status >= http.HTTPStatus.INTERNAL_SERVER_ERROR or status in _TEMPORARY:
            raise SourceUnavailableError(msg)
        raise UnexpectedFormatError(msg)
    msg = f"cannot download {url}: more than {MAX_REDIRECTS} redirects"
    raise UnexpectedFormatError(msg)


def _get_once(
    url: str, connect_timeout: float, read_timeout: float
) -> tuple[int, str, bytes, str | None]:
    """One GET: the status, its reason, the body and the Location header."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.netloc, timeout=connect_timeout)
    elif parts.scheme == "http":
        connection = http.client.HTTPConnection(parts.netloc, timeout=connect_timeout)
    else:
        msg = f"cannot download {url}: not an HTTP(S) address"
        raise UnexpectedFormatError(msg)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        connection.connect()
        # The constructor's timeout covers the connection; from here on, the reads.
        if connection.sock is not None:
            connection.sock.settimeout(read_timeout)
        connection.request("GET", path, headers={"User-Agent": "policy-rate-monitor"})
        response = connection.getresponse()
        body = response.read()
    except (OSError, http.client.HTTPException) as error:
        msg = f"cannot download {url}: {error}"
        raise SourceUnavailableError(msg) from error
    finally:
        connection.close()
    return response.status, response.reason, body, response.getheader("Location")


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links += [value for name, value in attrs if name == "href" and value]


def _find_download_url(source: SourceSection, get: Callable[[str], bytes]) -> str:
    page_url = str(source.discovery_url)
    fallback = str(source.fallback_url)
    try:
        page = get(page_url).decode("utf-8", errors="replace")
    except (SourceUnavailableError, UnexpectedFormatError) as error:
        logger.warning("%s; using the fallback URL", error)
        return fallback
    collector = _LinkCollector()
    collector.feed(page)
    for link in collector.links:
        url = urljoin(page_url, link)
        if urlsplit(url).path.rsplit("/", 1)[-1] == source.file_name:
            return url
    logger.warning(
        "no link to %s on %s; using the fallback URL", source.file_name, page_url
    )
    return fallback


def _download(
    url: str,
    attempts: int,
    get: Callable[[str], bytes],
    sleep: Callable[[float], None],
) -> bytes:
    # All attempts but the last retry after a growing pause (1 s, 2 s, ...); the last
    # one is outside the loop so that its error reaches the caller. Only a temporary
    # error is retried: an UnexpectedFormatError goes straight to the caller.
    for attempt in range(1, attempts):
        try:
            return get(url)
        except SourceUnavailableError as error:
            logger.warning("%s (attempt %d of %d); retrying", error, attempt, attempts)
            sleep(attempt)
    return get(url)


# What zipfile raises for bytes that are not a ZIP or a corrupt one: BadZipFile (also
# for a bad CRC-32); zlib.error, OSError (from bz2), LZMAError and EOFError for broken
# compressed data; ValueError for a bad offset in the ZIP's directory, and its subclass
# UnicodeDecodeError for a garbled file name; RuntimeError for an encrypted entry and
# NotImplementedError for an unsupported compression method. Only zipfile calls over
# bytes in memory run under it, so no bug elsewhere and no file-system error is caught.
_BAD_ZIP = (
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    EOFError,
    OSError,
    ValueError,
    RuntimeError,
    NotImplementedError,
)


def read_zip_csv(content: bytes, where: str) -> bytes:
    """Returns the one CSV file inside a ZIP, checked against its CRC-32.

    ``fetch`` checks a download with it and ``transform`` reads the cached copy with
    it, so both refuse the same files.

    Args:
        content: The bytes of the ZIP.
        where: The URL or path that names the ZIP in the error message.

    Returns:
        The bytes of the CSV file.

    Raises:
        UnexpectedFormatError: *content* is not a ZIP, is corrupt or encrypted, or
            does not hold exactly one CSV file.
    """
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = [
                name for name in archive.namelist() if name.lower().endswith(".csv")
            ]
            if len(names) != 1:
                msg = f"{where} should hold exactly one CSV file, found {len(names)}"
                raise UnexpectedFormatError(msg)
            return archive.read(names[0])
    except _BAD_ZIP as error:
        msg = f"{where} is not a readable ZIP file: {error}"
        raise UnexpectedFormatError(msg) from error


def _store(zip_path: Path, content: bytes, release: SourceRelease) -> None:
    # The record is renamed into place second. A download happens only when the old
    # record is missing or older than the cache lifetime, so a crash between the two
    # renames leaves an expired record and the next run downloads again.
    write_files(
        {
            zip_path: content,
            zip_path.with_suffix(".json"): release.model_dump_json(indent=2).encode(),
        }
    )
