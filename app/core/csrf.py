"""
Lightweight CSRF protection via same-origin verification + security headers.

For every state-changing request (POST, PUT, PATCH, DELETE) we verify that
the Origin/Referer header matches the request's Host header.

Exemptions:
  - Safe methods (GET, HEAD, OPTIONS)
  - Paths starting with /api/, /settings/, /users/, /routes/ (already
    protected by SameSite=lax session cookies)
  - /healthz, /readyz (internal probes)
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {"/healthz", "/readyz"}


class SameOriginCsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path
        if method in SAFE_METHODS or path in EXEMPT_PATHS or path.startswith(("/api/", "/settings/", "/users/", "/routes/")):
            return await call_next(request)

        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        source = origin or referer
        if not source:
            return _reject(request)

        from urllib.parse import urlsplit

        if "://" in source:
            parsed = urlsplit(source)
            source_host = parsed.netloc
        else:
            source_host = source

        if not source_host or source_host != host:
            return _reject(request)

        return await call_next(request)


def _reject(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "error": "csrf_failed",
            "detail": "Same-origin verification failed.",
            "path": request.url.path,
        },
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response