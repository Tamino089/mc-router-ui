"""
Authentication routes.
"""

import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.ratelimit import RateLimiter
from app.core.security import current_user, verify_password
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Per-IP throttle that makes credential stuffing meaningfully slower. Adequate
# for this single-process service; a multi-worker deployment would need a shared
# store instead.
login_limiter = RateLimiter(max_attempts=5, window_seconds=60)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _render_login(request: Request, error: str, status_code: int, username: str = ""):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "last_username": username,
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")},
    )


@router.post("/login")
async def login_post(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    ip = _client_ip(request)
    if await login_limiter.is_limited(ip):
        logger.warning("Login rate limit hit for %s", ip)
        return _render_login(
            request,
            "Too many attempts. Please wait a minute and try again.",
            429,
            username,
        )
    await login_limiter.record(ip)

    with get_db() as con:
        user_row = con.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()

    # PBKDF2 is deliberately CPU-heavy, so keep it off the event loop.
    authenticated = user_row and await asyncio.to_thread(
        verify_password, password, user_row["password_hash"]
    )

    if authenticated:
        # Clear any pre-login session state so a fixed session id cannot be
        # reused after authentication.
        request.session.clear()
        request.session["user"] = {
            "id": user_row["id"],
            "username": user_row["username"],
            "role": user_row["role"],
        }
        return RedirectResponse(url="/", status_code=303)

    return _render_login(request, "Invalid username or password.", 401, username)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
