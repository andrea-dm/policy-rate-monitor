"""The HTTP API: the same steps as the command line, over HTTP.

``create_app`` builds the FastAPI application around a configuration and a provider,
so tests pass the in-memory provider and ``bis-prates serve`` the bulk-file one. The
routes call ``service``, as the CLI does; they keep only the HTTP side: the query
parameters, the statuses and the jobs.

See also: [HTTP API](api.md), [decision 0014](adr/0014-http-api.md) and [the HTTP
routes](advanced/operations/workflows.md#http-routes).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import uvicorn
from fastapi import APIRouter, FastAPI, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from policy_rate_monitor import __version__
from policy_rate_monitor.chart.interactive import rate_figure
from policy_rate_monitor.config import requested_codes
from policy_rate_monitor.core.errors import MissingInputError
from policy_rate_monitor.report import summary_document
from policy_rate_monitor.service import (
    RefArea,
    StagedData,
    TimeSeries,
    load_staged,
    ref_areas,
    summary,
    timeseries,
    write_report,
)
from policy_rate_monitor.web.errors import ApiError, ErrorBody, add_error_handlers
from policy_rate_monitor.web.jobs import Job, JobRunner

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from policy_rate_monitor.config import DatasetConfig
    from policy_rate_monitor.providers import DataProvider

__all__ = ["DEFAULT_OUT_DIR", "ListenError", "ReportRequest", "create_app", "serve"]

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = Path("out")
"""Where a ``report`` job writes its files, unless told otherwise."""

_CODES = Query(description="Comma-separated codes, e.g. US,EA")

# The error responses each router can give, all with the same body, for /docs.
_READ_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorBody}
    for status in (
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        HTTPStatus.INTERNAL_SERVER_ERROR,
    )
}
_JOB_ERRORS: dict[int | str, dict[str, Any]] = {
    **_READ_ERRORS,
    HTTPStatus.SERVICE_UNAVAILABLE: {"model": ErrorBody},
}


class ListenError(Exception):
    """The HTTP API cannot listen on the address and port it was given."""


class ReportRequest(BaseModel):
    """The body of ``POST /jobs/report``.

    Attributes:
        countries: The codes of the countries or areas, as for ``/summary``.
        start: First date on the chart; ``None`` for the whole history.
    """

    model_config = ConfigDict(frozen=True)

    countries: list[str] = Field(min_length=1)
    start: date | None = None


class _Staged:
    """The staged data, read once and again after each ``transform``.

    Reads are served from memory. A ``transform`` replaces the data only once the
    new files are written, so a read during a transform gets the previous data.
    With nothing read yet, a read during a transform does not wait for it: it is
    told that nothing is staged yet.
    """

    def __init__(self, provider: DataProvider) -> None:
        super().__init__()
        self._provider = provider
        self._data: StagedData | None = None
        self._lock = threading.Lock()
        self._transforming = threading.Event()

    def get(self) -> StagedData:
        data = self._data
        if data is not None:
            return data
        if self._transforming.is_set():
            msg = "nothing is staged yet and a transform is running: poll its job"
            raise MissingInputError(msg)
        with self._lock:
            if self._data is None:
                self._data = load_staged(self._provider)
            return self._data

    def transform(self) -> StagedData:
        self._transforming.set()
        try:
            with self._lock:
                self._provider.transform()
                self._data = load_staged(self._provider)
                return self._data
        finally:
            self._transforming.clear()


def _codes(given: list[str]) -> list[str]:
    """Strips the codes, drops the empty ones and checks the rest."""
    try:
        return requested_codes(given)
    except ValueError as error:
        status = HTTPStatus.UNPROCESSABLE_ENTITY
        raise ApiError(status, "invalid_request", str(error)) from None


def _read_routes(config: DatasetConfig, staged: _Staged) -> APIRouter:
    router = APIRouter(tags=["data"], responses=_READ_ERRORS)

    @router.get("/ref-areas")
    def get_ref_areas() -> list[RefArea]:
        """The countries and economic areas the data has a series for."""
        return ref_areas(config, staged.get())

    @router.get("/summary")
    def get_summary(countries: Annotated[str, _CODES]) -> dict[str, Any]:
        """The latest rate, the rate 12 months earlier and the change, per code."""
        codes = _codes(countries.split(","))
        data = staged.get()
        snapshots = summary(config, data, codes)
        generated_at = datetime.now(UTC).replace(microsecond=0)
        return summary_document(config, snapshots, data.release, generated_at)

    @router.get("/timeseries")
    def get_timeseries(
        country: Annotated[str, Query(description="One code, e.g. US or EA")],
        start: date | None = None,
    ) -> TimeSeries:
        """The valid observations of one country or economic area."""
        [code] = _codes([country])
        found = timeseries(config, staged.get(), code, start)
        if found is None:
            msg = f"no series for {code}; GET /ref-areas lists the codes"
            raise ApiError(HTTPStatus.NOT_FOUND, "not_found", msg)
        return found

    @router.get("/chart")
    def get_chart(
        countries: Annotated[str, _CODES], start: date | None = None
    ) -> Response:
        """The policy-rate chart as a Plotly figure, in JSON."""
        codes = _codes(countries.split(","))
        data = staged.get()
        snapshots = summary(config, data, codes)
        figure = rate_figure(config.dataset.title, snapshots, data.observations, start)
        return Response(content=figure.to_json(), media_type="application/json")

    return router


def _job_routes(
    config: DatasetConfig,
    provider: DataProvider,
    staged: _Staged,
    jobs: JobRunner,
    out_dir: Path,
) -> APIRouter:
    router = APIRouter(prefix="/jobs", tags=["jobs"], responses=_JOB_ERRORS)

    def accepted(response: Response, job: Job) -> Job:
        response.headers["Location"] = f"/jobs/{job.id}"
        return job

    @router.post("/fetch", status_code=HTTPStatus.ACCEPTED)
    def post_fetch(response: Response) -> Job:
        """Starts downloading the source file, or reusing a recent copy."""

        def work() -> dict[str, Any]:
            return {"release": provider.fetch().model_dump(mode="json")}

        return accepted(response, jobs.submit("fetch", work))

    @router.post("/transform", status_code=HTTPStatus.ACCEPTED)
    def post_transform(response: Response) -> Job:
        """Starts staging the downloaded file; the read endpoints then serve it."""

        def work() -> dict[str, Any]:
            data = staged.transform()
            return {
                "observations": len(data.observations),
                "series": len(data.series),
                "findings": len(data.issues),
            }

        return accepted(response, jobs.submit("transform", work))

    @router.post("/report", status_code=HTTPStatus.ACCEPTED)
    def post_report(request: ReportRequest, response: Response) -> Job:
        """Starts writing the report files, as ``bis-prates report`` does."""
        codes = _codes(request.countries)

        def work() -> dict[str, Any]:
            data = staged.get()
            snapshots = summary(config, data, codes)
            paths = write_report(config, data, snapshots, out_dir, request.start)
            return {"files": [str(path) for path in paths]}

        return accepted(response, jobs.submit("report", work))

    @router.get("/{job_id}")
    def get_job(job_id: str) -> Job:
        """A job's status, with its result or its error once it has finished."""
        job = jobs.get(job_id)
        if job is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "not_found", f"no job {job_id}")
        return job

    return router


def create_app(
    config: DatasetConfig, provider: DataProvider, out_dir: Path = DEFAULT_OUT_DIR
) -> FastAPI:
    """Builds the HTTP API of one dataset.

    Args:
        config: The dataset configuration.
        provider: Where the data comes from.
        out_dir: Where a ``report`` job writes its files.

    Returns:
        The application; ``app.state.jobs`` is its ``JobRunner``.
    """
    staged = _Staged(provider)
    jobs = JobRunner()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            # Waits for the running job, off the event loop so signals still work.
            await asyncio.to_thread(jobs.shutdown)

    app = FastAPI(
        title=config.dataset.title,
        version=__version__,
        description="Policy rates by country or economic area, and the pipeline jobs.",
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    add_error_handlers(app)
    app.include_router(_read_routes(config, staged))
    app.include_router(_job_routes(config, provider, staged, jobs, out_dir))
    return app


def serve(
    config: DatasetConfig,
    provider: DataProvider,
    *,
    host: str,
    port: int,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> None:
    """Runs the HTTP API with uvicorn until it is stopped.

    The socket is bound here, before uvicorn starts, so that a port in use is a
    plain error instead of uvicorn ending the process with its own exit code.

    Args:
        config: The dataset configuration.
        provider: Where the data comes from.
        host: The address to listen on, e.g. ``127.0.0.1``; one with a colon is
            taken as an IPv6 address.
        port: The port to listen on, e.g. 8000.
        out_dir: Where a ``report`` job writes its files.

    Raises:
        ListenError: The address and port cannot be bound, e.g. the port is in use.
    """
    app = create_app(config, provider, out_dir)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        sock = socket.create_server((host, port), family=family)
    except OSError as error:
        msg = f"cannot listen on {host}, port {port}: {error.strerror or error}"
        raise ListenError(msg) from error
    with sock:
        # uvicorn logs its address only for the sockets it binds itself.
        logger.info("listening on %s, port %d", host, sock.getsockname()[1])
        uvicorn.Server(uvicorn.Config(app, host=host, port=port)).run(sockets=[sock])
