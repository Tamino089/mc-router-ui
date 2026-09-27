"""
Crafty Controller API endpoints.
"""

import asyncio
import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import crafty, mc_router
from app.services.health import tcp_check
from app.services.sse import broadcast

logger = logging.getLogger(__name__)

router = APIRouter()

# Crafty server identifiers are UUIDs; constraining them keeps the value safe to
# interpolate into an upstream URL path.
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

CRAFTY_ACTION_MAP = {
    "start": "start_server",
    "stop": "stop_server",
    "restart": "restart_server",
}


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _authorize(request: Request, permission: str):
    user = current_user(request)
    if not user:
        return _error("Not authenticated", 401)
    if user.get("role") != "admin" and not user_has_perm(user, permission):
        return _error("Permission denied", 403)
    return None


def _valid_server_id(server_id: str) -> bool:
    return bool(SERVER_ID_RE.match(server_id or ""))


def _as_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _crafty_container_host() -> str:
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key='crafty_container_host'"
        ).fetchone()
    return row[0] if row else ""


@router.get("/api/crafty/servers")
async def get_crafty_servers(request: Request):
    error = _authorize(request, "see_servers")
    if error:
        return error

    data, err = await crafty.crafty_request("get", "/servers")
    if err:
        # "Not configured" is a normal empty state; anything else is a failure
        # that should not be reported as success.
        configured = "not configured" not in err.lower()
        if not configured:
            return {"success": True, "configured": False, "servers": []}
        logger.warning("Crafty server listing failed: %s", err)
        return _error(err, 502)

    if not isinstance(data, list):
        return _error("Unexpected response from Crafty", 502)

    container_host = _crafty_container_host()

    async def fetch_stats(server_id: str):
        stats, _ = await crafty.crafty_request("get", f"/servers/{server_id}/stats")
        return stats if isinstance(stats, dict) else {}

    stats_results = await asyncio.gather(
        *(fetch_stats(s.get("server_id")) for s in data if isinstance(s, dict))
    )

    servers = []
    server_dicts = [s for s in data if isinstance(s, dict)]
    for server, stats in zip(server_dicts, stats_results, strict=True):
        port = server.get("server_port")
        if port is None:
            port = stats.get("server_port")
        port = _as_port(port)
        running = bool(stats.get("running", False))

        port_reachable = False
        port_error = ""
        if running and port and container_host:
            port_reachable, _, port_error = await asyncio.to_thread(
                tcp_check, container_host, port, 1.5
            )

        servers.append(
            {
                "id": server.get("server_id"),
                "name": server.get("server_name"),
                "port": port,
                "running": running,
                "port_reachable": port_reachable,
                "port_error": port_error,
                "online_players": stats.get("online", 0),
                "max_players": stats.get("max", 0),
                "cpu": stats.get("cpu", 0.0),
                "mem_percent": stats.get("mem_percent", 0.0),
                "container_address": f"{container_host}:{port}" if container_host else "",
            }
        )

    return {
        "success": True,
        "configured": True,
        "servers": servers,
        "container_host": container_host,
    }


@router.post("/api/crafty/servers/{server_id}/action")
async def crafty_server_action(request: Request, server_id: str, action: str = Form(...)):
    error = _authorize(request, "manage_servers")
    if error:
        return error
    if not _valid_server_id(server_id):
        return _error("Invalid server id")

    crafty_cmd = CRAFTY_ACTION_MAP.get(action)
    if not crafty_cmd:
        return _error(f"Invalid action: {action}")

    _, err = await crafty.crafty_request(
        "post", f"/servers/{server_id}/action/{crafty_cmd}"
    )
    if err:
        # Older Crafty builds accept the raw action name instead.
        _, retry_err = await crafty.crafty_request(
            "post", f"/servers/{server_id}/action/{action}"
        )
        if retry_err:
            logger.warning("Crafty action %s failed for %s: %s", action, server_id, err)
            return _error(err, 502)

    return {
        "success": True,
        "message": f"Server {action} signal sent successfully",
    }


@router.post("/api/crafty/servers/{server_id}/port")
async def crafty_change_port(
    request: Request,
    server_id: str,
    port: int = Form(...),
    restart: bool = Form(False),
):
    error = _authorize(request, "manage_servers")
    if error:
        return error
    if not _valid_server_id(server_id):
        return _error("Invalid server id")
    if not 1024 <= port <= 65535:
        return _error("Port must be between 1024 and 65535")

    logger.info("Port change requested: server=%s port=%d restart=%s", server_id, port, restart)

    server_data, err = await crafty.crafty_request("get", f"/servers/{server_id}")
    if err:
        logger.warning("Could not fetch details for %s: %s", server_id, err)
        return _error(f"Could not fetch server details: {err}", 502)

    server_data = server_data if isinstance(server_data, dict) else {}
    server_name = server_data.get("server_name")

    stats_data, _ = await crafty.crafty_request("get", f"/servers/{server_id}/stats")
    is_running = (stats_data or {}).get("running", False)

    stop_failed = False
    if restart and is_running:
        logger.info("Stopping server %s before changing its port", server_id)
        _, stop_err = await crafty.crafty_request(
            "post", f"/servers/{server_id}/action/stop_server"
        )
        if stop_err:
            # Rewriting server.properties while the server still runs would be
            # overwritten on its next shutdown.
            stop_failed = True
            logger.warning("Could not stop %s before the port change: %s", server_id, stop_err)
        else:
            await asyncio.sleep(5)

    # Filesystem work is blocking, so keep it off the event loop.
    prop_path = await asyncio.to_thread(
        crafty.get_server_properties_path, server_id, server_name
    )
    file_updated = False
    if prop_path:
        file_updated = await asyncio.to_thread(
            crafty.update_server_properties_port, prop_path, port
        )
    else:
        logger.warning(
            "No server.properties found for %s; is the volume mounted?", server_id
        )

    _, api_err = await crafty.crafty_request(
        "patch", f"/servers/{server_id}", json={"server_port": port}
    )
    api_updated = not api_err
    if api_err:
        logger.warning("Crafty API port update failed for %s: %s", server_id, api_err)

    if not file_updated and not api_updated:
        message = (
            f"Could not locate or update server.properties for server "
            f"'{server_name or server_id}'. Ensure the Crafty server directory is "
            f"mounted at /crafty/servers."
        )
        return _error(message, 500)

    container_host = _crafty_container_host()
    old_port = _as_port(server_data.get("server_port") or server_data.get("port"))

    impacted: list[dict] = []
    if container_host and old_port:
        old_backend = f"{container_host}:{old_port}"
        new_backend = f"{container_host}:{port}"
        # Rewrite the rows first, then sync mc-router outside the transaction so
        # the write lock is not held across network calls.
        with get_db() as con:
            rows = con.execute(
                "SELECT id, hostname, is_default FROM routes WHERE backend=?",
                (old_backend,),
            ).fetchall()
            for row in rows:
                con.execute(
                    "UPDATE routes SET backend=? WHERE id=?", (new_backend, row["id"])
                )
            con.commit()
        impacted = [dict(r) for r in rows]
    elif not container_host:
        logger.warning(
            "CRAFTY_CONTAINER_HOST is not set, so routes could not be re-pointed "
            "after the port change"
        )

    routes_updated = 0
    for route in impacted:
        if route["is_default"]:
            sync_err = await mc_router.push_default(f"{container_host}:{port}")
        else:
            sync_err = await mc_router.push_route(route["hostname"], f"{container_host}:{port}")
        if sync_err:
            logger.warning(
                "mc-router sync failed for route %s after the port change: %s",
                route["id"],
                sync_err,
            )
        else:
            routes_updated += 1

    if restart and not stop_failed:
        logger.info("Restarting server %s", server_id)
        await crafty.crafty_request(
            "post", f"/servers/{server_id}/action/start_server"
        )

    if routes_updated:
        await broadcast("route-change", {"action": "edit", "reason": "crafty_port_change"})

    notes = [
        "Server restarted." if restart and not stop_failed else "Restart the server to apply."
    ]
    if stop_failed:
        notes.append("The server could not be stopped, so it may overwrite the change on shutdown.")
    if routes_updated:
        notes.append(f"{routes_updated} route(s) updated in mc-router.")

    logger.info(
        "Port change completed: server=%s port=%d routes=%d",
        server_id,
        port,
        routes_updated,
    )
    return JSONResponse(
        {
            "success": True,
            "message": f"Port changed to {port}. " + " ".join(notes),
            "file_updated": file_updated,
            "file_path": str(prop_path) if prop_path else None,
            "api_updated": api_updated,
            "routes_updated": routes_updated,
        }
    )
