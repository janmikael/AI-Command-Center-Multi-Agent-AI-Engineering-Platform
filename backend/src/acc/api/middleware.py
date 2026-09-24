"""Pure ASGI middleware: request id, access log, security headers."""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_QUIET_PATHS = {"/healthz", "/readyz"}

access_log = structlog.get_logger("acc.access")

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        path: str = scope["path"]
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if path.startswith("/api/"):
                    headers.setdefault("cache-control", "no-store")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                log = access_log.debug if path in _QUIET_PATHS else access_log.info
                client = scope.get("client")
                log(
                    "http_request",
                    method=scope["method"],
                    path=path,
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    client_ip=client[0] if client else None,
                )
