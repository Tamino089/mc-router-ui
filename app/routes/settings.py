import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import MIN_PASSWORD_LENGTH
from app.core.ratelimit import RateLimiter
from app.core.security import current_user, hash_password, verify_password
from app.core.validation import normalize_base_url
from app.db.database import get_db
from app.db.schema import DEFAULT_PASSWORD_SETTING, user_has_perm
from app.routes import set_flash
from app.services import cloudflare

router = APIRouter()

password_change_limiter = RateLimiter(max_attempts=5, window_seconds=60)

SETTINGS_URL = "/?tab=settings"

def _redirect_with_flash(request: Request, kind: str, message: str, url: str = SETTINGS_URL):
    set_flash(request, kind, message)
    return RedirectResponse(url=url, status_code=303)

@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    limiter_key = f"user:{user['id']}"
    if await password_change_limiter.is_limited(limiter_key):
        return _redirect_with_flash(
            request, "error", "Too many attempts. Please wait a minute and try again."
        )

    if len(new_password) < MIN_PASSWORD_LENGTH:
        await password_change_limiter.record(limiter_key)
        return _redirect_with_flash(
            request,
            "error",
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if new_password != confirm_password:
        await password_change_limiter.record(limiter_key)
        return _redirect_with_flash(request, "error", "New passwords do not match")

    with get_db() as con:
        row = con.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()

    if not row or not await asyncio.to_thread(
        verify_password, current_password, row["password_hash"]
    ):
        await password_change_limiter.record(limiter_key)
        return _redirect_with_flash(request, "error", "Incorrect current password")

    hashed = await asyncio.to_thread(hash_password, new_password)
    with get_db() as con:
        con.execute(
            "UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"])
        )

        con.execute(
            "DELETE FROM settings WHERE key=?", (DEFAULT_PASSWORD_SETTING,)
        )
        con.commit()

    return _redirect_with_flash(request, "success", "Password changed successfully.")

@router.post("/settings/cloudflare")
async def save_cloudflare_settings(
    request: Request,
    cf_token: str = Form(""),
    cf_zone_id: str = Form(""),
    cf_zone_name: str = Form(""),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_cloudflare"):
        return _redirect_with_flash(request, "error", "Permission denied")

    token = cf_token.strip()
    with get_db() as con:

        if token:
            con.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token',?)",
                (token,),
            )
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_id',?)",
            (cf_zone_id.strip(),),
        )
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name',?)",
            (cf_zone_name.strip(),),
        )
        con.commit()

    cloudflare.invalidate_zone_cache()
    return _redirect_with_flash(request, "success", "Cloudflare configuration saved")

@router.post("/settings/crafty")
async def save_crafty_settings(
    request: Request,
    crafty_url: str = Form(""),
    crafty_token: str = Form(""),
    crafty_container_host: str = Form(""),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_settings"):
        return _redirect_with_flash(request, "error", "Permission denied")

    raw_url = crafty_url.strip()
    url = normalize_base_url(raw_url) if raw_url else ""
    if raw_url and not url:
        return _redirect_with_flash(
            request,
            "error",
            "Crafty URL must be a valid http:// or https:// URL without credentials",
        )

    token = crafty_token.strip()
    with get_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)", (url,)
        )
        if token:
            con.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)",
                (token,),
            )
        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)",
            (crafty_container_host.strip(),),
        )
        con.commit()

    return _redirect_with_flash(request, "success", "Crafty configuration saved")

@router.post("/settings/wizard")
async def save_wizard_settings(
    request: Request,
    cf_token: str = Form(""),
    cf_zone: str = Form(""),
    crafty_url: str = Form(""),
    crafty_token: str = Form(""),
    skip: str = Form(""),
):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return _redirect_with_flash(request, "error", "Permission denied")

    raw_url = crafty_url.strip()
    url = normalize_base_url(raw_url) if raw_url else ""
    if raw_url and not url:
        return _redirect_with_flash(
            request,
            "error",
            "Crafty URL must be a valid http:// or https:// URL without credentials",
        )

    with get_db() as con:
        if not skip:
            if cf_token:
                con.execute(
                    "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token',?)",
                    (cf_token.strip(),),
                )
            if cf_zone:
                zone = cf_zone.strip()

                if len(zone) == 32 and "." not in zone:
                    con.execute(
                        "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_id',?)",
                        (zone,),
                    )
                else:
                    con.execute(
                        "INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name',?)",
                        (zone,),
                    )
            if url and crafty_token:
                con.execute(
                    "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)",
                    (url,),
                )
                con.execute(
                    "INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)",
                    (crafty_token.strip(),),
                )
                con.execute(
                    "INSERT OR REPLACE INTO settings (key,value) "
                    "VALUES ('crafty_container_host',?)",
                    ("",),
                )

        con.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('setup_wizard_done',?)",
            ("1",),
        )
        con.commit()

    cloudflare.invalidate_zone_cache()
    return _redirect_with_flash(request, "success", "Setup completed", "/")
