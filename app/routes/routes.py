"""
CRUD operations for Minecraft routes.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import cloudflare, mc_router
from app.services.health import tcp_check

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_form_or_json(request: Request) -> dict:
    """Extract form data from either JSON body or form-encoded POST."""
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    try:
        form = await request.form()
        return {k: v for k, v in form.items()}
    except Exception:
        return {}


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
async def add_route(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
    if not user_has_perm(user, "create_route"):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data = await _get_form_or_json(request)
    hostname = data.get("hostname", "").strip().lower()
    backend = data.get("backend", "").strip()
    is_def = bool(data.get("is_default", False))

    if not backend:
        return JSONResponse({"success": False, "error": "Backend is required"}, status_code=400)
    if not hostname and not is_def:
        return JSONResponse({"success": False, "error": "Hostname is required"}, status_code=400)

    try:
        if not is_def:
            valid, v_err = await cloudflare.validate_domain(hostname, False)
            if not valid:
                return JSONResponse({"success": False, "error": v_err}, status_code=400)

        with get_db() as con:
            existing = con.execute(
                "SELECT id FROM routes WHERE hostname=?", (hostname,)
            ).fetchone()
            if existing:
                return JSONResponse({"success": False, "error": "Route already exists"}, status_code=409)

            cur = con.execute(
                "INSERT INTO routes (hostname, backend, is_default, owner_id) VALUES (?, ?, ?, ?)",
                (hostname, backend, int(is_def), user["id"]),
            )
            route_id = cur.lastrowid
            con.commit()

        if is_def:
            err = await mc_router.push_default(backend)
        else:
            err = await mc_router.push_route(hostname, backend)

        if err:
            return JSONResponse({"success": False, "error": f"Route saved but mc-router sync failed: {err}"}, status_code=500)

        dns_msg = ""
        if not is_def:
            cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)
            if cf_err:
                dns_msg = f" (DNS: {cf_err})"
            else:
                dns_msg = " (DNS record created/updated)"

        await _trigger_health_check(route_id, backend)

        return JSONResponse({"success": True, "message": f"Route added successfully{dns_msg}"})

    except Exception as e:
        logger.exception("Error adding route")
        return JSONResponse({"success": False, "error": f"Error adding route: {e}"}, status_code=500)


@router.post("/routes/edit/{route_id}")
async def edit_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    data = await _get_form_or_json(request)
    hostname = data.get("hostname", "").strip().lower()
    backend = data.get("backend", "").strip()
    is_def = bool(data.get("is_default", False))

    with get_db() as con:
        r_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r_row:
            return JSONResponse({"success": False, "error": "Route not found"}, status_code=404)

        if r_row["owner_id"] != user["id"] and user.get("role") != "admin":
            return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)
        if r_row["owner_id"] == user["id"] and not user_has_perm(user, "edit_own_route"):
            return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

        old_hostname = r_row["hostname"]
        old_is_default = bool(r_row["is_default"])

        if hostname != old_hostname:
            existing = con.execute("SELECT id FROM routes WHERE hostname=?", (hostname,)).fetchone()
            if existing:
                return JSONResponse({"success": False, "error": "Hostname already exists"}, status_code=409)

    if is_def:
        err = await mc_router.push_default(backend)
    else:
        err = await mc_router.push_route(hostname, backend)

    if err:
        return JSONResponse({"success": False, "error": f"Route updated but sync failed: {err}"}, status_code=500)

    if not old_is_default and old_hostname != hostname:
        await mc_router.delete_route(old_hostname)

    with get_db() as con:
        con.execute(
            "UPDATE routes SET hostname=?, backend=?, is_default=? WHERE id=?",
            (hostname, backend, int(is_def), route_id),
        )
        con.commit()

    cf_err = None
    if old_hostname != hostname and not old_is_default:
        await cloudflare.cf_delete_record_by_hostname(old_hostname)
    if not is_def:
        cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)

    await _trigger_health_check(route_id, backend)

    dns_msg = ""
    if not is_def and not cf_err:
        dns_msg = " (DNS synced)"
    elif cf_err:
        dns_msg = f" (DNS: {cf_err})"

    return JSONResponse({"success": True, "message": f"Route updated successfully{dns_msg}"})


@router.post("/routes/delete/{route_id}")
async def delete_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    with get_db() as con:
        r_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r_row:
            return JSONResponse({"success": False, "error": "Route not found"}, status_code=404)

        if r_row["owner_id"] != user["id"] and user.get("role") != "admin":
            return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)
        if r_row["owner_id"] == user["id"] and not user_has_perm(user, "delete_own_route"):
            return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

        hostname = r_row["hostname"]
        is_default = bool(r_row["is_default"])

        con.execute("DELETE FROM routes WHERE id=?", (route_id,))
        con.execute("DELETE FROM health_checks WHERE route_id=?", (route_id,))
        con.commit()

    if not is_default:
        err = await mc_router.delete_route(hostname)
        if err:
            logger.warning("Failed to delete route on router: %s", err)

    if not is_default:
        cf_err = await cloudflare.cf_delete_record_by_hostname(hostname)
        if cf_err:
            logger.warning("Failed to delete DNS record: %s", cf_err)

    return JSONResponse({"success": True, "message": "Route deleted successfully"})