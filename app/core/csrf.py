from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core import config

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {"/healthz", "/readyz"}

class SameOriginCsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path
        if method in SAFE_METHODS or path in EXEMPT_PATHS:
            return await call_next(request)

        source = request.headers.get("origin") or request.headers.get("referer")
        if not source:
            return _reject(request)

        source_host = urlsplit(source).netloc if "://" in source else source
        if not source_host or source_host != request.headers.get("host", ""):
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

CSP_TEMPLATE = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'nonce-{nonce}'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'"
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP_TEMPLATE.format(nonce=nonce)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if config.SESSION_HTTPS_ONLY:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response
