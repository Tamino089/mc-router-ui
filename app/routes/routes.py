import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core import config
from app.core.security import current_user
from app.core.validation import (
    HOSTNAME_RE,
    is_valid_backend,
    normalize_backend,
    parse_backend,
)
from app.db import schema
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.routes import get_form_or_json
from app.services import cloudflare, docker_watcher, mc_router
from app.services.health import tcp_check
from app.services.routes_data import build_routes_payload
from app.services.sse import broadcast

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_HOSTNAME = "__default__"

_mutation_lock = asyncio.Lock()

_background_tasks: set[asyncio.Task] = set()

def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)

def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}

def _can_manage_default(user: dict) -> bool:
    return user.get("role") == "admin" or user_has_perm(user, "manage_default_route")

def _dns_message(is_default: bool, cf_err: str | None) -> str:
    if is_default:
        return ""
    return f" (DNS: {cf_err})" if cf_err else " (DNS synced)"

def _prepare_backend(backend: str):
    if not backend:
        return None, _error("Backend is required")
    if not is_valid_backend(backend):
        return None, _error(
            "Backend must be a valid HOST:PORT "
            "(for example 192.168.1.1:25565 or mc.example.com:25566)"
        )
    backend = normalize_backend(backend)
    _, port = parse_backend(backend)
    if not 1 <= port <= 65535:
        return None, _error("Port must be between 1 and 65535")
    return backend, None

async def _resolve_hostname(raw_hostname: str, is_default: bool):
    if is_default:
        return raw_hostname or DEFAULT_HOSTNAME, None

    if not raw_hostname:
        return None, _error("Hostname is required")

    if "." in raw_hostname:
        hostname = raw_hostname
        if not HOSTNAME_RE.match(hostname):
            return None, _error(
                "Hostname must be a valid FQDN (for example play.example.com)"
            )
    else:

        cf_token, _, _ = cloudflare.get_cf_config()
        if not cf_token:
            return None, _error(
                "Cloudflare not configured. Provide a full FQDN "
                "(for example play.example.com) or configure Cloudflare in Settings."
            )
        hostname = await cloudflare.resolve_hostname(raw_hostname)
        if not hostname:
            return None, _error("Could not resolve hostname via Cloudflare.")
        if not HOSTNAME_RE.match(hostname):
            return None, _error("Resolved hostname is not a valid FQDN")

    valid, v_err = await cloudflare.validate_domain(hostname, is_default)
    if not valid:
        return None, _error(v_err)

    if await docker_watcher.is_docker_managed(hostname):
        return None, _error(
            f"'{hostname}' is managed by a Docker container label "
            "and cannot be edited here",
            409,
        )

    return hostname, None

def _schedule_health_check(route_id: int, backend: str) -> None:
    task = asyncio.create_task(_store_health_check(route_id, backend))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

async def _store_health_check(route_id: int, backend: str) -> None:
    try:
        host, port = parse_backend(backend)
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
        logger.warning(
            "Immediate health check failed for route %s", route_id, exc_info=True
        )

@router.post("/routes/add")
async def add_route(request: Request):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)
    if not user_has_perm(user, "create_route"):
        return _error("Permission denied", 403)

    data = await get_form_or_json(request)
    raw_hostname = str(data.get("hostname", "")).strip().lower()
    is_default = _as_bool(data.get("is_default", False))

    if is_default and not _can_manage_default(user):
        return _error("Permission denied for the default route", 403)

    backend, err = _prepare_backend(str(data.get("backend", "")).strip())
    if err:
        return err

    hostname, err = await _resolve_hostname(raw_hostname, is_default)
    if err:
        return err

    async with _mutation_lock:
        with get_db() as con:
            if con.execute(
                "SELECT id FROM routes WHERE hostname=?", (hostname,)
            ).fetchone():
                return _error("Route already exists", 409)
            previous_defaults = [
                dict(r)
                for r in con.execute(
                    "SELECT id, backend FROM routes WHERE is_default=1"
                ).fetchall()
            ]

        dns_msg = ""
        dns_done = False
        if not is_default:
            cf_err = await cloudflare.sync_dns_for_route(hostname, is_default)
            if cf_err:
                dns_msg = f" (DNS: {cf_err})"
            else:
                dns_done = True
                dns_msg = " (DNS record created/updated)"

        if is_default:
            push_err = await mc_router.push_default(backend)
        else:
            push_err = await mc_router.push_route(hostname, backend)

        if push_err:
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            return _error(f"mc-router sync failed: {push_err}", 500)

        try:
            with get_db() as con:
                if is_default:

                    con.execute("UPDATE routes SET is_default=0 WHERE is_default=1")
                cur = con.execute(
                    """INSERT INTO routes (hostname, backend, is_default, source, owner_id)
                       VALUES (?, ?, ?, 'static', ?)""",
                    (hostname, backend, int(is_default), user["id"]),
                )
                route_id = cur.lastrowid
                con.commit()
        except Exception:
            logger.exception("Failed to persist route %s", hostname)
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            if is_default:
                for prev in previous_defaults:
                    await mc_router.push_default(prev["backend"])
            else:
                await mc_router.delete_route(hostname)
            return _error("Could not save the route; external changes were rolled back", 500)

    _schedule_health_check(route_id, backend)
    await broadcast(
        "route-change",
        {"action": "add", "route_id": route_id, "hostname": hostname},
    )

    return JSONResponse({"success": True, "message": f"Route added successfully{dns_msg}"})

@router.post("/routes/edit/{route_id}")
async def edit_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)

    data = await get_form_or_json(request)
    raw_hostname = str(data.get("hostname", "")).strip().lower()
    is_default = _as_bool(data.get("is_default", False))

    backend, err = _prepare_backend(str(data.get("backend", "")).strip())
    if err:
        return err

    hostname, err = await _resolve_hostname(raw_hostname, is_default)
    if err:
        return err

    async with _mutation_lock:
        with get_db() as con:
            row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
            if not row:
                return _error("Route not found", 404)
            if row["source"] == "docker":
                return _error(
                    "This route is managed by Docker labels and cannot be edited", 403
                )

            was_default = bool(row["is_default"])
            if (is_default or was_default) and not _can_manage_default(user):
                return _error("Permission denied for the default route", 403)
            if not was_default and (
                user.get("role") != "admin"
                and (
                    row["owner_id"] != user["id"]
                    or not user_has_perm(user, "edit_own_route")
                )
            ):
                return _error("Permission denied", 403)

            hostname_changed = hostname != row["hostname"]
            if hostname_changed and con.execute(
                "SELECT id FROM routes WHERE hostname=?", (hostname,)
            ).fetchone():
                return _error("Hostname already exists", 409)

            old_hostname = row["hostname"]
            old_backend = row["backend"]

        if await docker_watcher.is_docker_managed(old_hostname):
            return _error(
                f"'{old_hostname}' is managed by a Docker container label "
                "and cannot be edited here",
                409,
            )

        dns_done = False
        cf_err = None
        if not is_default:
            cf_err = await cloudflare.sync_dns_for_route(hostname, is_default)
            dns_done = not cf_err

        if is_default:
            push_err = await mc_router.push_default(backend)
        else:
            push_err = await mc_router.push_route(hostname, backend)

        if push_err:
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            return JSONResponse(
                {
                    "success": False,
                    "status": "PARTIAL_FAILURE",
                    "errors": [{"code": "MC_ROUTER_SYNC_FAILED", "message": push_err}],
                },
                status_code=502,
            )

        if not was_default and not is_default and hostname_changed:
            del_err = await mc_router.delete_route(old_hostname)
            if del_err:
                logger.warning(
                    "Failed to delete old route %s from mc-router: %s",
                    old_hostname,
                    del_err,
                )

        try:
            with get_db() as con:
                if is_default:
                    con.execute(
                        "UPDATE routes SET is_default=0 WHERE is_default=1 AND id!=?",
                        (route_id,),
                    )
                con.execute(
                    "UPDATE routes SET hostname=?, backend=?, is_default=? WHERE id=?",
                    (hostname, backend, int(is_default), route_id),
                )
                con.commit()
        except Exception:
            logger.exception("Failed to update route %s", route_id)
            if dns_done:
                await cloudflare.cf_delete_record_by_hostname(hostname)
            if is_default:
                restore_err = await mc_router.push_default(old_backend)
            else:
                await mc_router.delete_route(hostname)
                restore_err = await mc_router.push_route(old_hostname, old_backend)
            if restore_err:
                logger.error("Failed to restore route after edit: %s", restore_err)
            return _error("Failed to update route; changes were rolled back", 500)

        if dns_done and hostname_changed and not is_default:
            await cloudflare.cf_delete_record_by_hostname(old_hostname)

    _schedule_health_check(route_id, backend)
    await broadcast(
        "route-change",
        {"action": "edit", "route_id": route_id, "hostname": hostname},
    )

    return JSONResponse(
        {
            "success": True,
            "message": f"Route updated successfully{_dns_message(is_default, cf_err)}",
        }
    )

@router.post("/routes/delete/{route_id}")
async def delete_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)

    async with _mutation_lock:
        with get_db() as con:
            row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
            if not row:
                return _error("Route not found", 404)
            if row["source"] == "docker":
                return _error(
                    "This route is managed by Docker labels and cannot be deleted", 403
                )

            is_default = bool(row["is_default"])
            if is_default and not _can_manage_default(user):
                return _error("Permission denied for the default route", 403)
            if not is_default and (
                user.get("role") != "admin"
                and (
                    row["owner_id"] != user["id"]
                    or not user_has_perm(user, "delete_own_route")
                )
            ):
                return _error("Permission denied", 403)

            hostname = row["hostname"]

            con.execute("DELETE FROM routes WHERE id=?", (route_id,))
            con.commit()

        warning = None
        if is_default:

            if await mc_router.clear_default():
                warning = (
                    "The fallback backend could not be cleared on mc-router and "
                    "may stay active until it restarts."
                )
                logger.warning("Failed to clear the default route on mc-router")
        else:
            del_err = await mc_router.delete_route(hostname)
            if del_err:
                logger.warning("Failed to delete route on router: %s", del_err)
            cf_err = await cloudflare.cf_delete_record_by_hostname(hostname)
            if cf_err:
                logger.warning("Failed to delete DNS record: %s", cf_err)

    await broadcast(
        "route-change",
        {"action": "delete", "route_id": route_id, "hostname": hostname},
    )

    message = "Route deleted successfully"
    if warning:
        message = f"{message}. {warning}"
    return JSONResponse({"success": True, "message": message})

@router.get("/api/routes")
async def list_routes(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("role") == "admin":
        user_perms = set(config.ALL_PERMISSIONS)
    else:
        user_perms = schema.get_user_perms(user["id"])
    routes_data = await build_routes_payload(user, user_perms)
    return JSONResponse({"success": True, "routes": routes_data})
