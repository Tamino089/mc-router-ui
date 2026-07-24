"""
CRUD operations for Minecraft routes.
"""

import logging

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import RedirectResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import cloudflare, mc_router

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/routes/add")
async def add_route(
    request: Request,
    hostname: str = Form(...),
    backend: str = Form(...),
    is_default: str = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not user_has_perm(user, "create_route"):
        return RedirectResponse(url="/?error=Permission denied", status_code=303)

    hostname = hostname.strip().lower()
    backend = backend.strip()
    is_def = bool(is_default)

    if not backend:
        return RedirectResponse(url="/?error=Backend is required", status_code=303)
    if not hostname and not is_def:
        return RedirectResponse(url="/?error=Hostname is required", status_code=303)

    try:
        with get_db() as con:
            existing = con.execute(
                "SELECT id FROM routes WHERE hostname=?", (hostname,)
            ).fetchone()
            if existing:
                return RedirectResponse(url="/?error=Route already exists", status_code=303)

            con.execute(
                "INSERT INTO routes (hostname, backend, is_default, owner_id) VALUES (?, ?, ?, ?)",
                (hostname, backend, int(is_def), user["id"]),
            )
            con.commit()

        # Sync to router
        if is_def:
            err = await mc_router.push_default(backend)
        else:
            err = await mc_router.push_route(hostname, backend)

        if err:
            return RedirectResponse(url=f"/?error=Route saved but mc-router failed: {err}", status_code=303)

        # Sync DNS
        cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)
        if cf_err:
            return RedirectResponse(url=f"/?success=Route added&error=Cloudflare DNS failed: {cf_err}", status_code=303)

        return RedirectResponse(url="/?success=Route added successfully", status_code=303)

    except Exception as e:
        logger.exception("Error adding route")
        return RedirectResponse(url=f"/?error=Error adding route: {e}", status_code=303)


@router.post("/routes/edit/{route_id}")
async def edit_route(
    request: Request,
    route_id: int,
    hostname: str = Form(...),
    backend: str = Form(...),
    is_default: str = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    hostname = hostname.strip().lower()
    backend = backend.strip()
    is_def = bool(is_default)

    with get_db() as con:
        r_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r_row:
            return RedirectResponse(url="/?error=Route not found", status_code=303)

        if r_row["owner_id"] != user["id"] and user.get("role") != "admin":
            return RedirectResponse(url="/?error=Permission denied", status_code=303)
        if r_row["owner_id"] == user["id"] and not user_has_perm(user, "edit_own_route"):
            return RedirectResponse(url="/?error=Permission denied", status_code=303)

        old_hostname = r_row["hostname"]
        old_is_default = bool(r_row["is_default"])

        if hostname != old_hostname:
            existing = con.execute("SELECT id FROM routes WHERE hostname=?", (hostname,)).fetchone()
            if existing:
                return RedirectResponse(url="/?error=Hostname already exists", status_code=303)

        con.execute(
            "UPDATE routes SET hostname=?, backend=?, is_default=? WHERE id=?",
            (hostname, backend, int(is_def), route_id),
        )
        con.commit()

    # Sync to router
    if not old_is_default:
        await mc_router.delete_route(old_hostname)

    if is_def:
        err = await mc_router.push_default(backend)
    else:
        err = await mc_router.push_route(hostname, backend)

    if err:
        return RedirectResponse(url=f"/?error=Route updated but sync failed: {err}", status_code=303)

    # Sync DNS
    cf_err = None
    if old_hostname != hostname and not old_is_default:
        await cloudflare.cf_delete_record_by_hostname(old_hostname)
    if not is_def:
        cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)

    if cf_err:
        return RedirectResponse(url=f"/?success=Route updated&error=Cloudflare error: {cf_err}", status_code=303)

    return RedirectResponse(url="/?success=Route updated successfully", status_code=303)


@router.post("/routes/delete/{route_id}")
async def delete_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    with get_db() as con:
        r_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r_row:
            return RedirectResponse(url="/?error=Route not found", status_code=303)

        if r_row["owner_id"] != user["id"] and user.get("role") != "admin":
            return RedirectResponse(url="/?error=Permission denied", status_code=303)
        if r_row["owner_id"] == user["id"] and not user_has_perm(user, "delete_own_route"):
            return RedirectResponse(url="/?error=Permission denied", status_code=303)

        hostname = r_row["hostname"]
        is_default = bool(r_row["is_default"])

        con.execute("DELETE FROM routes WHERE id=?", (route_id,))
        con.commit()

    # Sync to router
    if not is_default:
        err = await mc_router.delete_route(hostname)
        if err:
            logger.warning("Failed to delete route on router: %s", err)

    # Sync DNS
    if not is_default:
        cf_err = await cloudflare.cf_delete_record_by_hostname(hostname)
        if cf_err:
            logger.warning("Failed to delete DNS record: %s", cf_err)

    return RedirectResponse(url="/?success=Route deleted successfully", status_code=303)
