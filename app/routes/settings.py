"""
Settings and password changes.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.security import current_user, verify_password, hash_password
from app.db.database import get_db

router = APIRouter()


@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if len(new_password) < 6:
        return RedirectResponse(url="/?pw_error=Password must be at least 6 characters&tab=settings", status_code=303)

    with get_db() as con:
        r_user = con.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not r_user or not verify_password(current_password, r_user["password_hash"]):
            return RedirectResponse(url="/?pw_error=Incorrect current password&tab=settings", status_code=303)

        hashed = hash_password(new_password)
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"]))
        con.commit()

    return RedirectResponse(url="/?pw_success=1&tab=settings", status_code=303)


@router.post("/settings/cloudflare")
async def save_cloudflare_settings(
    request: Request,
    cf_token: str = Form(""),
    cf_zone_id: str = Form(""),
    cf_zone_name: str = Form(""),
):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_api_token',?)", (cf_token.strip(),))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_id',?)", (cf_zone_id.strip(),))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('cf_zone_name',?)", (cf_zone_name.strip(),))
        con.commit()

    return RedirectResponse(url="/?success=Cloudflare configuration saved&tab=settings", status_code=303)


@router.post("/settings/crafty")
async def save_crafty_settings(
    request: Request,
    crafty_url: str = Form(...),
    crafty_token: str = Form(...),
    crafty_container_host: str = Form(None),
):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    url = crafty_url.strip()
    token = crafty_token.strip()
    chost = crafty_container_host.strip() if crafty_container_host else "crafty"

    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_url',?)", (url,))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_token',?)", (token,))
        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)", (chost,))
        con.commit()

    return RedirectResponse(url="/?success=Crafty configuration saved&tab=settings", status_code=303)


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
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

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
                con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('crafty_container_host',?)", ("crafty",))

        con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('setup_wizard_done',?)", ("1",))
        con.commit()

    return RedirectResponse(url="/?success=Setup completed", status_code=303)
