"""Download the source ZIP and keep a cached copy of it.

``fetch`` finds the file's link on the configured discovery page, downloads it, checks
that it is a ZIP holding exactly one CSV, and stores it in ``<data_dir>/raw/`` next to a
small JSON file that records where it came from (a ``SourceRelease``). A copy younger
than the cache lifetime is used without any request; when the network fails, an older
copy is used with a warning.

See also: [How it works](how-it-works.md), sections 1 and 2, and
[Data model](data-model.md).
"""

from __future__ import annotations

import hashlib
import http
import http.client
import io
import logging
import time
import zipfile
from datetime import UTC, datetime, timedelta
from functools import partial
from html.parser import HTMLParser
from typing import TYPE_CHECKING, override
from urllib.parse import urljoin, urlsplit

from pydantic import ValidationError

from policy_rate_monitor.core.errors import (
    SourceUnavailableError,
    UnexpectedFormatError,
)
from policy_rate_monitor.core.models import SourceRelease

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from policy_rate_monitor.config.models import SourceSection

__all__ = ["cached_zip", "fetch", "http_get", "read_release"]

logger = logging.getLogger(__name__)


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
        missing, or the record cannot be read.
    """
    zip_path = cached_zip(source, data_dir)
    meta_path = zip_path.with_suffix(".json")
    if not zip_path.is_file() or not meta_path.is_file():
        return None
    try:
        return SourceRelease.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except ValidationError:
        logger.warning("%s is unreadable; the cached copy is ignored", meta_path)
        return None


def fetch(
    source: SourceSection,
    data_dir: Path,
    get: Callable[[str], bytes] | None = None,
) -> SourceRelease:
    """Makes sure a recent copy of the source ZIP is cached, and returns its release.

    Args:
        source: The ``source`` section of the configuration.
        data_dir: The data directory; the ZIP goes to its ``raw/`` folder.
        get: Downloads one URL and returns the body. Defaults to ``http_get`` with the
            configured timeouts; tests pass a stand-in.

    Returns:
        The release of the cached copy: a fresh download, or the cached one when it is
        younger than the cache lifetime or the network is down.

    Raises:
        SourceUnavailableError: Nothing can be downloaded and nothing is cached.
        UnexpectedFormatError: The download is not a ZIP holding exactly one CSV. The
            cached copy, if any, is kept.
    """
    download = get or partial(
        http_get,
        connect_timeout=source.connect_timeout_seconds,
        read_timeout=source.read_timeout_seconds,
    )
    cached = read_release(source, data_dir)
    if cached is not None:
        age = datetime.now(UTC) - cached.retrieved_at
        if age < timedelta(hours=source.cache_ttl_hours):
            logger.info("using the cached copy from %s", cached.retrieved_at)
            return cached

    try:
        url = _find_download_url(source, download)
        content = _download(url, source.max_attempts, download)
    except SourceUnavailableError as error:
        if cached is None:
            raise
        logger.warning("%s; using the cached copy from %s", error, cached.retrieved_at)
        return cached

    _check_zip(content, url)
    release = SourceRelease(
        location=url,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at=datetime.now(UTC),
    )
    _store(cached_zip(source, data_dir), content, release)
    logger.info("downloaded %s (%d bytes)", url, len(content))
    return release


def http_get(url: str, connect_timeout: float, read_timeout: float) -> bytes:
    """Downloads one URL over HTTP(S), with separate connect and read timeouts.

    Args:
        url: The address to download.
        connect_timeout: Seconds to wait for the connection.
        read_timeout: Seconds to wait for each read once connected.

    Returns:
        The body of the response.

    Raises:
        SourceUnavailableError: The server cannot be reached or does not answer 200.
    """
    parts = urlsplit(url)
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.netloc, timeout=connect_timeout)
    else:
        connection = http.client.HTTPConnection(parts.netloc, timeout=connect_timeout)
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
    if response.status != http.HTTPStatus.OK:
        msg = f"cannot download {url}: HTTP {response.status} {response.reason}"
        raise SourceUnavailableError(msg)
    return body


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
    except SourceUnavailableError as error:
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


def _download(url: str, attempts: int, get: Callable[[str], bytes]) -> bytes:
    # All attempts but the last retry after a growing pause (1 s, 2 s, ...); the last
    # one is outside the loop so that its error reaches the caller.
    for attempt in range(1, attempts):
        try:
            return get(url)
        except SourceUnavailableError as error:
            logger.warning("%s (attempt %d of %d); retrying", error, attempt, attempts)
            time.sleep(attempt)
    return get(url)


def _check_zip(content: bytes, url: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            broken = archive.testzip()
    except zipfile.BadZipFile as error:
        msg = f"{url} is not a ZIP file"
        raise UnexpectedFormatError(msg) from error
    if broken is not None:
        msg = f"{url}: {broken} is corrupt in the ZIP"
        raise UnexpectedFormatError(msg)
    csv_files = [name for name in names if name.lower().endswith(".csv")]
    if len(csv_files) != 1:
        msg = f"{url} should hold exactly one CSV file, found {len(csv_files)}"
        raise UnexpectedFormatError(msg)


def _store(zip_path: Path, content: bytes, release: SourceRelease) -> None:
    # Write next to the target, then rename: a failure never leaves a half-written ZIP.
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    partial = zip_path.with_name(zip_path.name + ".part")
    partial.write_bytes(content)
    partial.replace(zip_path)
    # The record goes second. A download happens only when the old record is missing or
    # older than the cache lifetime, so a crash between the two writes leaves an
    # expired record and the next run downloads again.
    zip_path.with_suffix(".json").write_text(
        release.model_dump_json(indent=2), encoding="utf-8"
    )
