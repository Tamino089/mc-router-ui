"""
Settings and password changes.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.ratelimit import RateLimiter
from app.core.security import current_user, verify_password, hash_password
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.routes import set_flash

router = APIRouter()

# Brute-force throttle on the password-change endpoint, keyed per user id.
# The "current password" field is the credential an attacker would try to
# guess, so limit how often it may be wrong within a short window.
password_change_limiter = RateLimiter(max_attempts=5, window_seconds=60)


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
        set_flash(request, "error", "Too many attempts. Please wait a minute and try again.")
        return RedirectResponse(url="/?tab=settings", status_code=303)

    if len(new_password) < 6:
        await password_change_limiter.record(limiter_key)
        set_flash(request, "error", "Password must be at least 6 characters")
        return RedirectResponse(url="/?tab=settings", status_code=303)
    if new_password != confirm_password:
        await password_change_limiter.record(limiter_key)
        set_flash(request, "error", "New passwords do not match")
        return RedirectResponse(url="/?tab=settings", status_code=303)

    with get_db() as con:
        r_user = con.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not r_user or not verify_password(current_password, r_user["password_hash"]):
            await password_change_limiter.record(limiter_key)
            set_flash(request, "error", "Incorrect current password")
            return RedirectResponse(url="/?tab=settings", status_code=303)

        hashed = hash_password(new_password)
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"]))
        con.commit()

    set_flash(request, "success", "Password changed successfully.")
    return RedirectResponse(url="/?tab=settings", status_code=303)


@router.post("/settings/cloudflare")
async def save_cloudflare_settings(
    request: Request,
    cf_token: str = Form(""),
    cf_zone_id: str = Form(""),
    cf_zone_name: str = Form(""),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_cloudflare"):
        set_flash(request, "error", "Permission denied")
        return RedirectResponse(url="/?tab=settings", status_code=303)

    token = cf_token.strip()
    with get_db() as con:
        # Blank field means "leave unchanged" — never overwrite an existing
        # token with an empty string just because the form re-rendered it.
        if token:
            con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token',?)", (token,))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_id',?)", (cf_zone_id.strip(),))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name',?)", (cf_zone_name.strip(),))
        con.commit()

    set_flash(request, "success", "Cloudflare configuration saved")
    return RedirectResponse(url="/?tab=settings", status_code=303)


@router.post("/settings/crafty")
async def save_crafty_settings(
    request: Request,
    crafty_url: str = Form(...),
    crafty_token: str = Form(...),
    crafty_container_host: str = Form(None),
):
    user = current_user(request)
    if not user or not user_has_perm(user, "manage_settings"):
        set_flash(request, "error", "Permission denied")
        return RedirectResponse(url="/?tab=settings", status_code=303)

    url = crafty_url.strip()
    token = crafty_token.strip()
    chost = crafty_container_host.strip() if crafty_container_host else ""

    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)", (url,))
        # Blank field means "leave unchanged" — never overwrite an existing
        # token with an empty string just because the form re-rendered it.
        if token:
            con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)", (token,))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)", (chost,))
        con.commit()

    set_flash(request, "success", "Crafty configuration saved")
    return RedirectResponse(url="/?tab=settings", status_code=303)


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
        set_flash(request, "error", "Permission denied")
        return RedirectResponse(url="/?tab=settings", status_code=303)

    with get_db() as con:
        if not skip:
            if cf_token:
                con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token',?)", (cf_token.strip(),))
            if cf_zone:
                zone = cf_zone.strip()
                # Zone ID is 32 hex chars with no dots; otherwise treat as domain name
                if len(zone) == 32 and "." not in zone:
                    con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_id',?)", (zone,))
                else:
                    con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name',?)", (zone,))

            if crafty_url and crafty_token:
                con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)", (crafty_url.strip(),))
                con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)", (crafty_token.strip(),))
                con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)", ("",))

        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('setup_wizard_done',?)", ("1",))
        con.commit()

    set_flash(request, "success", "Setup completed")
    return RedirectResponse(url="/", status_code=303)
