"""
Lightweight CSRF protection via same-origin verification.

This is a defense against Cross-Site Request Forgery that does NOT require
modifying existing forms or adding tokens. For every state-changing request
(POST, PUT, PATCH, DELETE) we verify that the `Origin` header (falling back to
`Referer`) matches the request's own `Host` header. Modern browsers send
`Origin` on all cross-origin unsafe requests, so a missing/mismatched Origin is
treated as a violation for non-GET requests.

Exemptions:
  - Safe methods (GET, HEAD, OPTIONS) — read-only.
  - The `/healthz` and `/readyz` paths — internal probes.

This is intentionally simpler than double-submit tokens; it pairs well with
Starlette's `SameSite=lax` session cookie (the default) which already blocks
most cross-site cookie carriage.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {"/healthz", "/readyz"}


class SameOriginCsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        if method in SAFE_METHODS or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        # If neither Origin nor Referer is present we cannot prove same-origin.
        # Treat as a violation for unsafe methods.
        source = origin or referer
        if not source:
            return _reject(request)

        # Extract the host portion of the source URL and compare to Host header.
        # source may be: "https://host:port/path" or "host:port"
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
