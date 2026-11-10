"""Consistent JSON errors.

Every error response has the same shape:

    {"detail": "<human-readable message>", "code": "<machine-readable code>"}

`detail` is what FastAPI already returned, so existing clients keep working; `code` is
stable for clients to branch on. Validation errors keep FastAPI's list of field errors
in `detail`. Unexpected exceptions return a generic 500 and are logged, never echoed,
so stack traces and internals don't leak to clients.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
    504: "upstream_timeout",
}


class ApiError(StarletteHTTPException):
    """An HTTP error with a specific code, e.g. ApiError(404, "player_not_found", "...")."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def code_for(status: int) -> str:
    return STATUS_CODES.get(status, "error")


def error_response(status: int, detail, code: str | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code or code_for(status)}, status_code=status, headers=headers)


async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, exc.detail, getattr(exc, "code", None), getattr(exc, "headers", None))


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Drop the echoed input: it can be large, and there's no need to reflect it back.
    errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
    return error_response(422, errors, "invalid_request")


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(500, "Something went wrong on the server. Try again in a moment.", "internal_error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)
