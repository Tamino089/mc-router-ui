"""
CRUD operations for Minecraft routes.
"""

import asyncio
import logging

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import RedirectResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import cloudflare, mc_router
from app.services.health import tcp_check

logger = logging.getLogger(__name__)

router = APIRouter()


async def _trigger_health_check(route_id: int, backend: str):
    """Run an immediate TCP health check for a single route and store the result."""
    try:
        parts = backend.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 25565
        healthy, latency = await asyncio.to_thread(tcp_check, host, port, 3.0)
        with get_db() as con:
            con.execute(
                """INSERT INTO health_checks (route_id, healthy, latency_ms, checked_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(route_id) DO UPDATE SET
                       healthy=excluded.healthy,
                       latency_ms=excluded.latency_ms,
                       checked_at=excluded.checked_at""",
                (route_id, int(healthy), latency if healthy else None),
            )
            con.commit()
    except Exception:
        logger.warning("Immediate health check failed for route %s", route_id, exc_info=True)


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
        # 1. Validate domain against Cloudflare zone (if configured)
        if not is_def:
            valid, v_err = await cloudflare.validate_domain(hostname, False)
            if not valid:
                return RedirectResponse(url=f"/?error={v_err}", status_code=303)

        # 2. Insert into DB
        with get_db() as con:
            existing = con.execute(
                "SELECT id FROM routes WHERE hostname=?", (hostname,)
            ).fetchone()
            if existing:
                return RedirectResponse(url="/?error=Route already exists", status_code=303)

            cur = con.execute(
                "INSERT INTO routes (hostname, backend, is_default, owner_id) VALUES (?, ?, ?, ?)",
                (hostname, backend, int(is_def), user["id"]),
            )
            route_id = cur.lastrowid
            con.commit()

        # 3. Push to mc-router
        if is_def:
            err = await mc_router.push_default(backend)
        else:
            err = await mc_router.push_route(hostname, backend)

        if err:
            return RedirectResponse(url=f"/?error=Route saved but mc-router failed: {err}", status_code=303)

        # 4. Sync DNS — surface result to user
        dns_msg = ""
        if not is_def:
            cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)
            if cf_err:
                dns_msg = f" (DNS: {cf_err})"
            else:
                dns_msg = " (DNS record created/updated)"

        # 5. Trigger immediate health check so the UI shows status right away
        await _trigger_health_check(route_id, backend)

        return RedirectResponse(url=f"/?success=Route added successfully{dns_msg}", status_code=303)

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

    # Push NEW route to mc-router BEFORE committing DB change
    if is_def:
        err = await mc_router.push_default(backend)
    else:
        err = await mc_router.push_route(hostname, backend)

    if err:
        return RedirectResponse(url=f"/?error=Route updated but sync failed: {err}", status_code=303)

    # Delete OLD route from mc-router (only if hostname changed or was non-default)
    if not old_is_default and old_hostname != hostname:
        await mc_router.delete_route(old_hostname)

    # Now commit DB change
    with get_db() as con:
        con.execute(
            "UPDATE routes SET hostname=?, backend=?, is_default=? WHERE id=?",
            (hostname, backend, int(is_def), route_id),
        )
        con.commit()

    # Sync DNS
    cf_err = None
    if old_hostname != hostname and not old_is_default:
        await cloudflare.cf_delete_record_by_hostname(old_hostname)
    if not is_def:
        cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)

    # Trigger immediate health check
    await _trigger_health_check(route_id, backend)

    dns_msg = ""
    if not is_def and not cf_err:
        dns_msg = " (DNS synced)"
    elif cf_err:
        dns_msg = f" (DNS: {cf_err})"

    return RedirectResponse(url=f"/?success=Route updated successfully{dns_msg}", status_code=303)


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
        con.execute("DELETE FROM health_checks WHERE route_id=?", (route_id,))
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
