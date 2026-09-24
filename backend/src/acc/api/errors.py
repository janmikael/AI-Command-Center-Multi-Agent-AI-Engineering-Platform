"""RFC 9457 problem+json responses for every error path."""

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from acc.domain.errors import (
    AuthenticationFailed,
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)

log = structlog.get_logger("acc.api")

PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFound: 404,
    Conflict: 409,
    ValidationFailed: 422,
    AuthenticationFailed: 401,
    PermissionDenied: 403,
    RateLimited: 429,
}


def problem(
    status: int,
    title: str,
    *,
    code: str,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"type": f"urn:acc:problem:{code}", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        body["request_id"] = request_id
    body.update(extra or {})
    return JSONResponse(
        jsonable_encoder(body), status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers
    )


def _status_for(exc: DomainError) -> int:
    for cls in type(exc).__mro__:
        if cls in _STATUS_BY_ERROR:
            return _STATUS_BY_ERROR[cls]
    return 400


async def _domain_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    headers = {"retry-after": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
    return problem(
        _status_for(exc),
        exc.message,
        code=exc.code,
        headers=headers,
        extra={k: v for k, v in exc.details.items() if k != "retry_after"},
    )


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem(
        422, "Request validation failed", code="request_validation", extra={"errors": errors}
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return problem(
        exc.status_code,
        str(exc.detail),
        code=f"http_{exc.status_code}",
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error_type=type(exc).__name__)
    return problem(500, "Internal server error", code="internal")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
