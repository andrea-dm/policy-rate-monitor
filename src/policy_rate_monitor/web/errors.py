"""The errors of the HTTP API: one kind per failure, its HTTP status, one JSON body.

Every error response, and every failed job, carries ``{"error": kind, "message": ...}``
and never a traceback. The typed errors of ``core`` map to kinds the way the CLI maps
them to exit codes.

See also: [HTTP API](api.md#errors).
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

from policy_rate_monitor.core.errors import (
    ConfigError,
    MissingInputError,
    PolicyRateMonitorError,
    SourceUnavailableError,
    UnexpectedFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request

__all__ = ["ApiError", "ErrorBody", "add_error_handlers", "error_body", "status_for"]

logger = logging.getLogger(__name__)

_KINDS: Final[dict[type[BaseException], tuple[str, HTTPStatus]]] = {
    ConfigError: ("config_error", HTTPStatus.INTERNAL_SERVER_ERROR),
    MissingInputError: ("missing_input", HTTPStatus.CONFLICT),
    SourceUnavailableError: ("source_unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
    UnexpectedFormatError: ("unexpected_format", HTTPStatus.INTERNAL_SERVER_ERROR),
    # The file system refusing a read or a write (permissions, a full disk).
    OSError: ("file_error", HTTPStatus.INTERNAL_SERVER_ERROR),
}


class ErrorBody(BaseModel):
    """The body of an error response, and the error of a failed job.

    Attributes:
        error: The kind of failure, e.g. ``missing_input``.
        message: What went wrong, for a person to read.
    """

    model_config = ConfigDict(frozen=True)

    error: str
    message: str


class ApiError(Exception):
    """An error a route raises on purpose: a missing resource, a job already running."""

    def __init__(self, status: HTTPStatus, error: str, message: str) -> None:
        """Keeps the HTTP status and the body.

        Args:
            status: The HTTP status, e.g. 404.
            error: The kind of failure, e.g. ``not_found``.
            message: What went wrong.
        """
        super().__init__(message)
        self.status = status
        self.body = ErrorBody(error=error, message=message)


def _kind(error: BaseException) -> tuple[str, HTTPStatus] | None:
    for cls in type(error).__mro__:
        if cls in _KINDS:
            return _KINDS[cls]
    return None


def error_body(error: BaseException) -> ErrorBody:
    """Describes an error for a response or a job, without its traceback.

    An error not raised on purpose is a bug: its traceback is logged, and the body
    names only its type.

    Args:
        error: The error.

    Returns:
        The kind and the message.

    Examples:
        >>> error_body(MissingInputError("run transform first"))
        ErrorBody(error='missing_input', message='run transform first')
    """
    if isinstance(error, ApiError):
        return error.body
    found = _kind(error)
    if found is None:
        logger.error("unexpected error, please report it", exc_info=error)
        return ErrorBody(error="internal_error", message=type(error).__name__)
    return ErrorBody(error=found[0], message=str(error))


def status_for(error: BaseException) -> HTTPStatus:
    """Returns the HTTP status of an error.

    Args:
        error: The error.

    Returns:
        The status of an ``ApiError`` or of a typed error, else 500.

    Examples:
        >>> status_for(MissingInputError("run transform first"))
        <HTTPStatus.CONFLICT: 409>
        >>> status_for(KeyError("x"))
        <HTTPStatus.INTERNAL_SERVER_ERROR: 500>
    """
    if isinstance(error, ApiError):
        return error.status
    found = _kind(error)
    return HTTPStatus.INTERNAL_SERVER_ERROR if found is None else found[1]


def _response(
    status: int, body: ErrorBody, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def add_error_handlers(app: FastAPI) -> None:
    """Makes every error response ``{"error": kind, "message": ...}``.

    That covers the errors raised on purpose, the typed errors, a file error, an
    invalid request, an unknown route and any bug.

    Args:
        app: The application.
    """

    @app.exception_handler(ApiError)
    @app.exception_handler(PolicyRateMonitorError)
    @app.exception_handler(OSError)
    @app.exception_handler(Exception)
    def _known(_request: Request, error: Exception) -> JSONResponse:
        return _response(status_for(error), error_body(error))

    @app.exception_handler(RequestValidationError)
    def _invalid_request(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        body = ErrorBody(error="invalid_request", message=problems)
        return _response(HTTPStatus.UNPROCESSABLE_ENTITY, body)

    @app.exception_handler(HTTPException)
    def _http_error(_request: Request, error: HTTPException) -> JSONResponse:
        not_found = error.status_code == HTTPStatus.NOT_FOUND
        body = ErrorBody(
            error="not_found" if not_found else "http_error", message=str(error.detail)
        )
        # Keeps the headers, e.g. Allow on a 405.
        return _response(error.status_code, body, error.headers)
