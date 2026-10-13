"""Security building blocks: headers, rate limiting, upload size cap, API-key auth."""

import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# --------------------------------------------------------------------------- headers
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
# Swagger UI / ReDoc load their JS and CSS from a CDN, so the docs pages need a looser policy.
_DOCS_CSP = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "worker-src blob:; frame-ancestors 'none'"
)
# The bundled web UI only loads its own script/style, and only talks to this server.
_UI_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defensive headers and a request id to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        is_docs = request.url.path.startswith(_DOCS_PATHS)
        is_ui = request.url.path == "/" or request.url.path.startswith("/ui/")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = _DOCS_CSP if is_docs else _UI_CSP if is_ui else _API_CSP
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Request-ID"] = uuid.uuid4().hex
        return response


# ------------------------------------------------------------------- rate limiting
class SlidingWindowLimiter:
    """Tiny in-memory sliding-window limiter (single process; use Redis/gateway to scale out)."""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            self._calls += 1
            if self._calls % 1000 == 0:
                self._purge(now)
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now)
            return True, 0

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
            del self._hits[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, general_limit: int, upload_limit: int):
        super().__init__(app)
        self.general = SlidingWindowLimiter(general_limit) if general_limit > 0 else None
        self.upload = SlidingWindowLimiter(upload_limit) if upload_limit > 0 else None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api"):
            client = request.client.host if request.client else "unknown"
            checks = []
            if self.general:
                checks.append((self.general, client))
            if self.upload and request.method == "POST":
                checks.append((self.upload, client))
            for limiter, key in checks:
                allowed, retry_after = limiter.check(key)
                if not allowed:
                    return JSONResponse(
                        {"detail": "Too many requests. Please slow down."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
        return await call_next(request)


# ------------------------------------------------------------------ upload size cap
class UploadSizeLimitMiddleware:
    """Rejects oversized uploads from the headers alone, before the body is read.

    Requests without a Content-Length (chunked encoding) are refused with 411 so
    the multipart parser can never be fed an unbounded stream.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        # multipart framing adds a little overhead on top of the file itself
        self.max_bytes = max_bytes + 64 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].startswith("/api/files"):
            headers = dict(scope["headers"])
            length = headers.get(b"content-length")
            problem: tuple[int, str] | None = None
            if length is None:
                problem = (411, "Content-Length header is required for uploads")
            elif not length.isdigit():
                problem = (400, "Invalid Content-Length header")
            elif int(length) > self.max_bytes:
                problem = (413, "Upload exceeds the maximum allowed size")
            if problem:
                response = JSONResponse({"detail": problem[1]}, status_code=problem[0])
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ------------------------------------------------------------------------ API keys
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="API key (if enabled)")


def require_api_key(request: Request, provided: str | None = Security(_api_key_header)) -> None:
    """Dependency: enforce `X-API-Key` when API_KEYS is configured (constant-time comparison)."""
    valid_keys = request.app.state.settings.api_key_list
    if not valid_keys:
        return  # authentication disabled (local development)
    candidate = (provided or "").encode()
    matches = [secrets.compare_digest(candidate, key.encode()) for key in valid_keys]
    if not provided or not any(matches):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )