"""
CRUD operations for Minecraft routes.
"""

import asyncio
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import cloudflare, docker_watcher, mc_router
from app.services.health import tcp_check
from app.services.sse import broadcast

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Strict validation patterns ────────────────────────────────────────────────
HOSTNAME_RE = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
)
SOCKET_ADDR_RE = re.compile(
    r'^[a-zA-Z0-9.-]+:[0-9]{1,5}$'
)
IP_PORT_RE = re.compile(
    r'^(\d{1,3}\.){3}\d{1,3}:\d{1,5}$'
)

def _valid_ip_port(s: str) -> bool:
    """Validate IP:PORT with proper octet ranges (0-255 each)."""
    m = IP_PORT_RE.match(s)
    if not m:
        return False
    ip_part, port_part = s.rsplit(":", 1)
    try:
        octets = [int(o) for o in ip_part.split(".")]
        if any(o < 0 or o > 255 for o in octets):
            return False
        port = int(port_part)
        return 1 <= port <= 65535
    except ValueError:
        return False


def _as_bool(value) -> bool:
    """Normalize JSON booleans and HTML form checkbox values."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


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
        healthy, latency, error = await asyncio.to_thread(tcp_check, host, port, 3.0)
        with get_db() as con:
            con.execute(
                """INSERT INTO health_checks (route_id, healthy, latency_ms, checked_at, error)
                   VALUES (?, ?, ?, datetime('now'), ?)
                   ON CONFLICT(route_id) DO UPDATE SET
                       healthy=excluded.healthy,
                       latency_ms=excluded.latency_ms,
                       checked_at=excluded.checked_at,
                       error=excluded.error""",
                (route_id, int(healthy), latency if healthy else None, error or None),
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
    raw_hostname = data.get("hostname", "").strip().lower()
    backend = data.get("backend", "").strip()
    is_def = _as_bool(data.get("is_default", False))

    if not backend:
        return JSONResponse({"success": False, "error": "Backend is required"}, status_code=400)
    if not raw_hostname and not is_def:
        return JSONResponse({"success": False, "error": "Hostname is required"}, status_code=400)

    # ── Strict backend validation ────────────────────────────────────────────
    if not is_def and not SOCKET_ADDR_RE.match(backend) and not _valid_ip_port(backend):
        return JSONResponse(
            {"success": False, "error": "Backend must be a valid HOST:PORT (e.g. 192.168.1.1:25565 or mc.example.com:25566)"},
            status_code=400,
        )
    parts = backend.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        port = int(parts[1])
        if port < 1 or port > 65535:
            return JSONResponse({"success": False, "error": "Port must be between 1 and 65535"}, status_code=400)

    try:
        # Resolve subdomain-only hostname (e.g. "play" → "play.tamino089.com")
        hostname = raw_hostname
        if is_def:
            # Default routes use a sentinel so the UNIQUE constraint and
            # DNS-skip logic in sync_dns_for_route() work correctly.
            hostname = raw_hostname or "__default__"
        else:
            # If hostname contains dots, treat as full FQDN — no Cloudflare needed
            if "." in raw_hostname:
                hostname = raw_hostname
                # Validate FQDN format
                if not HOSTNAME_RE.match(hostname):
                    return JSONResponse(
                        {"success": False, "error": "Hostname must be a valid FQDN (e.g. play.example.com)"},
                        status_code=400,
                    )
                # Skip Cloudflare zone validation for full FQDNs
            else:
                # Subdomain-only — requires Cloudflare to resolve
                cf_token, _, _ = await cloudflare.get_cf_config()
                if not cf_token:
                    return JSONResponse(
                        {"success": False, "error": "Cloudflare not configured. Provide a full FQDN (e.g. play.example.com) or configure Cloudflare in Settings."},
                        status_code=400,
                    )
                hostname = await cloudflare.resolve_hostname(raw_hostname)
                if not hostname:
                    return JSONResponse(
                        {"success": False, "error": "Could not resolve hostname via Cloudflare."},
                        status_code=400,
                    )

                # Strict hostname validation for resolved name
                if not HOSTNAME_RE.match(hostname):
                    return JSONResponse(
                        {"success": False, "error": "Resolved hostname is not a valid FQDN"},
                        status_code=400,
                    )

                valid, v_err = await cloudflare.validate_domain(hostname, False)
                if not valid:
                    return JSONResponse({"success": False, "error": v_err}, status_code=400)

            # Block Docker-managed hostnames (for both FQDN and resolved)
            if await docker_watcher.is_docker_managed(hostname):
                return JSONResponse(
                    {"success": False, "error": f"'{hostname}' is managed by a Docker container label and cannot be edited here"},
                    status_code=409,
                )

        # Step 1: Create DNS record (rollback if later steps fail)
        dns_done = False
        dns_msg = ""
        if not is_def:
            cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)
            if cf_err:
                dns_msg = f" (DNS: {cf_err})"
            else:
                dns_done = True
                dns_msg = " (DNS record created/updated)"

        # Step 2: Push to mc-router
        sync_warn = ""
        if is_def:
            err = await mc_router.push_default(backend)
        else:
            err = await mc_router.push_route(hostname, backend)
        if err:
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            return JSONResponse({"success": False, "error": f"mc-router sync failed: {err}"}, status_code=500)

        # Step 3: Save to DB (final — source of truth)
        try:
            with get_db() as con:
                existing = con.execute(
                    "SELECT id FROM routes WHERE hostname=?", (hostname,)
                ).fetchone()
                if existing:
                    if dns_done:
                        await cloudflare.cf_delete_record_by_hostname(hostname)
                    if not is_def:
                        await mc_router.delete_route(hostname)
                    return JSONResponse({"success": False, "error": "Route already exists"}, status_code=409)

                if is_def:
                    old_default = con.execute(
                        "SELECT id, backend FROM routes WHERE is_default=1"
                    ).fetchone()
                    if old_default:
                        con.execute(
                            "UPDATE routes SET is_default=0 WHERE id=?",
                            (old_default["id"],),
                        )

                cur = con.execute(
                    "INSERT INTO routes (hostname, backend, is_default, source, owner_id) VALUES (?, ?, ?, 'static', ?)",
                    (hostname, backend, int(is_def), user["id"]),
                )
                route_id = cur.lastrowid
                con.commit()
        except Exception:
            # Rollback DNS and mc-router
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            if not is_def:
                await mc_router.delete_route(hostname)
            else:
                # For default routes: push previous default back or leave orphan
                logger.warning("DB save failed after mc-router default was set — mc-router may have orphan default route")
            raise

        await _trigger_health_check(route_id, backend)

        # Broadcast route change via SSE
        await broadcast("route-change", {"action": "add", "route_id": route_id, "hostname": hostname})

        resp = {"success": True, "message": f"Route added successfully{dns_msg}"}
        if sync_warn:
            resp["warning"] = sync_warn
        return JSONResponse(resp)

    except Exception as e:
        logger.exception("Error adding route")
        return JSONResponse({"success": False, "error": f"Error adding route: {e}"}, status_code=500)


@router.post("/routes/edit/{route_id}")
async def edit_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    data = await _get_form_or_json(request)
    raw_hostname = data.get("hostname", "").strip().lower()
    backend = data.get("backend", "").strip()
    is_def = _as_bool(data.get("is_default", False))

    hostname = raw_hostname
    if is_def:
        hostname = raw_hostname or "__default__"
    elif hostname:
        # If hostname contains dots, treat as full FQDN — no Cloudflare needed
        if "." in raw_hostname:
            hostname = raw_hostname
            # Validate FQDN format
            if not HOSTNAME_RE.match(hostname):
                return JSONResponse(
                    {"success": False, "error": "Hostname must be a valid FQDN (e.g. play.example.com)"},
                    status_code=400,
                )
        else:
            # Subdomain-only — requires Cloudflare to resolve
            cf_token, _, _ = await cloudflare.get_cf_config()
            if not cf_token:
                return JSONResponse(
                    {"success": False, "error": "Cloudflare not configured. Provide a full FQDN (e.g. play.example.com) or configure Cloudflare in Settings."},
                    status_code=400,
                )
            hostname = await cloudflare.resolve_hostname(raw_hostname)
            if not hostname:
                return JSONResponse(
                    {"success": False, "error": "Could not resolve hostname via Cloudflare."},
                    status_code=400,
                )

            # Strict hostname validation for resolved name
            if not HOSTNAME_RE.match(hostname):
                return JSONResponse(
                    {"success": False, "error": "Resolved hostname is not a valid FQDN"},
                    status_code=400,
                )

            valid, v_err = await cloudflare.validate_domain(hostname, False)
            if not valid:
                return JSONResponse({"success": False, "error": v_err}, status_code=400)

        # Block Docker-managed hostnames (for both FQDN and resolved)
        if await docker_watcher.is_docker_managed(hostname):
            return JSONResponse(
                {"success": False, "error": f"'{hostname}' is managed by a Docker container label and cannot be edited here"},
                status_code=409,
            )

    # Read existing route + validate ownership + check hostname uniqueness
    # in a single transaction to prevent TOCTOU races
    #
    # cf_err/dns_done are defined here (not inside the try) so that if an
    # exception is raised before they're assigned below, the `except` block's
    # rollback logic doesn't itself crash with a NameError and mask the real
    # error.
    cf_err = None
    dns_done = False
    try:
        with get_db() as con:
            r_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
            if not r_row:
                return JSONResponse({"success": False, "error": "Route not found"}, status_code=404)

            if r_row["source"] == "docker":
                return JSONResponse(
                    {"success": False, "error": "This route is managed by Docker labels and cannot be edited"},
                    status_code=403,
                )

            if r_row["owner_id"] != user["id"] and user.get("role") != "admin":
                return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)
            if r_row["owner_id"] == user["id"] and not user_has_perm(user, "edit_own_route"):
                return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

            old_hostname = r_row["hostname"]
            old_is_default = bool(r_row["is_default"])
            old_backend = r_row["backend"]

            if hostname != old_hostname:
                existing = con.execute("SELECT id FROM routes WHERE hostname=?", (hostname,)).fetchone()
                if existing:
                    return JSONResponse({"success": False, "error": "Hostname already exists"}, status_code=409)

            # Step 1: Create NEW DNS record BEFORE deleting old one
            cf_err = None
            dns_done = False
            if not is_def:
                cf_err = await cloudflare.sync_dns_for_route(hostname, is_def)
                dns_done = not cf_err

            if dns_done and old_hostname != hostname and not old_is_default:
                await cloudflare.cf_delete_record_by_hostname(old_hostname)

            # Step 2: Push to mc-router (hard requirement — rollback DNS on failure)
            if is_def:
                err = await mc_router.push_default(backend)
            else:
                err = await mc_router.push_route(hostname, backend)
            if err:
                if dns_done:
                    await cloudflare.cf_delete_record_by_hostname(hostname)
                return JSONResponse({
                    "success": False,
                    "status": "PARTIAL_FAILURE",
                    "errors": [{"code": "MC_ROUTER_SYNC_FAILED", "message": err}],
                }, status_code=502)

            # Delete OLD route from mc-router (only if hostname changed)
            if not old_is_default and old_hostname != hostname:
                del_err = await mc_router.delete_route(old_hostname)
                if del_err:
                    logger.warning("Failed to delete old route %s from mc-router: %s", old_hostname, del_err)

            # Step 3: Save to DB — within the same transaction as the read
            con.execute(
                "UPDATE routes SET hostname=?, backend=?, is_default=? WHERE id=?",
                (hostname, backend, int(is_def), route_id),
            )
            con.commit()
    except Exception:
        logger.exception("Error editing route %d", route_id)
        # Best-effort rollback: DNS new record + mc-router push
        if not is_def and dns_done:
            await cloudflare.cf_delete_record_by_hostname(hostname)
        if not is_def:
            await mc_router.delete_route(hostname)
        return JSONResponse({"success": False, "error": "Failed to update route — changes rolled back"}, status_code=500)

    await _trigger_health_check(route_id, backend)
    await broadcast("route-change", {"action": "edit", "route_id": route_id, "hostname": hostname})

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

        # ── Block deleting Docker-managed routes ─────────────────────────────
        if r_row["source"] == "docker":
            return JSONResponse(
                {"success": False, "error": "This route is managed by Docker labels and cannot be deleted"},
                status_code=403,
            )

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

    # Broadcast route change via SSE
    await broadcast("route-change", {"action": "delete", "route_id": route_id, "hostname": hostname})

    return JSONResponse({"success": True, "message": "Route deleted successfully"})