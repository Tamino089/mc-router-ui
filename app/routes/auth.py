"""
Authentication routes (Login/Logout).
"""

import logging
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.security import current_user, verify_password
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ── Lightweight in-memory brute-force throttle ───────────────────────────────
# Not a substitute for a proper rate-limiter (e.g. Redis-backed) behind a
# multi-worker deployment, but this app is a single-process self-hosted
# service, so an in-memory counter per client IP is enough to make credential
# stuffing/brute-force meaningfully slower without adding a dependency.
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < _WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = attempts
    return len(attempts) >= _MAX_ATTEMPTS


def _record_attempt(ip: str) -> None:
    _LOGIN_ATTEMPTS.setdefault(ip, []).append(time.monotonic())


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _client_ip(request)
    if _is_rate_limited(ip):
        logger.warning("Login rate limit hit for %s", ip)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Too many attempts. Please wait a minute and try again."},
            status_code=429,
        )
    _record_attempt(ip)

    with get_db() as con:
        user_row = con.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()

    if user_row and verify_password(password, user_row["password_hash"]):
        request.session["user"] = {
            "id": user_row["id"],
            "username": user_row["username"],
            "role": user_row["role"],
        }
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password."},
        status_code=401,
    )


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
