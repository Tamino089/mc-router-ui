"""
Crafty Controller API endpoints.
"""

import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.schema import user_has_perm
from app.db.database import get_db
from app.services import crafty, mc_router
from app.services.health import tcp_check
from app.services.sse import broadcast

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/crafty/servers")
async def get_crafty_servers(request: Request):
    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "see_servers")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    data, err = await crafty.crafty_request("get", "/servers")
    if err:
        return JSONResponse({"success": True, "servers": [], "warning": err})

    with get_db() as con:
        chost_row = con.execute("SELECT value FROM settings WHERE key='crafty_container_host'").fetchone()
        chost = chost_row[0] if chost_row else "crafty"

    async def fetch_stats(server_id):
        stats_data, _ = await crafty.crafty_request("get", f"/servers/{server_id}/stats")
        return stats_data or {}

    tasks = [fetch_stats(s.get("server_id")) for s in data]
    stats_results = await asyncio.gather(*tasks)

    servers = []
    for s, stats in zip(data, stats_results):
        server_id = s.get("server_id")
        # In Crafty v4, stats are merged into the data dict of the /stats response
        port = s.get("server_port") or stats.get("server_port")
        running = stats.get("running", False)

        # TCP health check from our container to the game server
        port_reachable = False
        port_error = ""
        if running and port:
            port_reachable, _, port_error = await asyncio.to_thread(tcp_check, chost, int(port), 1.5)

        servers.append({
            "id": server_id,
            "name": s.get("server_name"),
            "port": port,
            "running": running,
            "port_reachable": port_reachable,
            "port_error": port_error,
            "online_players": stats.get("online", 0),
            "max_players": stats.get("max", 0),
            "cpu": stats.get("cpu", 0.0),
            "mem_percent": stats.get("mem_percent", 0.0),
            "container_address": f"{chost}:{port}",
        })

    return {"success": True, "servers": servers}


CRAFTY_ACTION_MAP = {
    "start": "start_server",
    "stop": "stop_server",
    "restart": "restart_server",
}


@router.post("/api/crafty/servers/{server_id}/action")
async def crafty_server_action(request: Request, server_id: str, action: str = Form(...)):
    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "manage_servers")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    crafty_cmd = CRAFTY_ACTION_MAP.get(action)
    if not crafty_cmd:
        return JSONResponse({"success": False, "error": f"Invalid action: {action}"}, status_code=400)

    _, err = await crafty.crafty_request("post", f"/servers/{server_id}/action/{crafty_cmd}")
    if err:
        # Fallback retry with raw action name if custom version
        _, err2 = await crafty.crafty_request("post", f"/servers/{server_id}/action/{action}")
        if err2:
            return JSONResponse({"success": False, "error": err}, status_code=500)

    return JSONResponse({"success": True, "message": f"Server {action} signal sent successfully"})


@router.post("/api/crafty/servers/{server_id}/port")
async def crafty_change_port(
    request: Request,
    server_id: str,
    port: int = Form(...),
    restart: bool = Form(False),
):
    user = current_user(request)
    if not user or (user.get("role") != "admin" and not user_has_perm(user, "manage_servers")):
        return JSONResponse({"success": False, "error": "Permission denied"}, status_code=403)

    if not (1024 <= port <= 65535):
        return JSONResponse({"success": False, "error": "Port must be between 1024 and 65535"}, status_code=400)

    logger.info("Port change request: server_id=%s -> port=%d (restart=%s)", server_id, port, restart)

    # 1) Fetch server metadata from Crafty
    logger.debug("Fetching server metadata for %s", server_id)
    server_data, err = await crafty.crafty_request("get", f"/servers/{server_id}")
    if err:
        logger.warning("Could not fetch server details for %s: %s", server_id, err)
        return JSONResponse({"success": False, "error": f"Could not fetch server details: {err}"}, status_code=500)

    server_name = (server_data or {}).get("server_name")

    # 2) Fetch live stats to see if server is running
    logger.debug("Fetching live stats for %s", server_id)
    stats_data, stats_err = await crafty.crafty_request("get", f"/servers/{server_id}/stats")
    is_running = (stats_data or {}).get("running", False)
    logger.debug("Server %s running=%s", server_id, is_running)

    # 3) Stop server if running and restart requested
    if restart and is_running:
        logger.info("Stopping server %s before port change", server_id)
        await crafty.crafty_request("post", f"/servers/{server_id}/action/stop_server")
        logger.debug("Waiting 5s for graceful shutdown of %s", server_id)
        await asyncio.sleep(5)

    # 4) Update server.properties on mounted volume
    logger.debug("Locating server.properties for %s", server_id)
    prop_path = crafty.get_server_properties_path(server_id, server_name)
    file_updated = False
    if prop_path:
        logger.info("Found server.properties at %s", prop_path)
        file_updated = crafty.update_server_properties_port(prop_path, port)
        logger.debug("server.properties updated=%s", file_updated)
    else:
        logger.warning("Could not find server.properties for %s; is the volume mounted?", server_id)

    # 5) Attempt Crafty API setting update (if API supported)
    logger.debug("Updating Crafty Controller database via API for %s", server_id)
    api_updated = False
    _, api_err = await crafty.crafty_request("patch", f"/servers/{server_id}", json={"server_port": port})
    if not api_err:
        api_updated = True
    else:
        logger.warning("Crafty API database update failed for %s: %s", server_id, api_err)

    if not file_updated and not api_updated:
        msg = f"Could not locate or update server.properties for server '{server_name or server_id}'. Ensure Crafty server directory is mounted to /crafty/servers."
        logger.error(msg)
        return JSONResponse({"success": False, "error": msg}, status_code=500)

    # 6) Update routes table + mc-router for any route pointing to this server
    chost_row = None
    with get_db() as con:
        chost_row = con.execute("SELECT value FROM settings WHERE key='crafty_container_host'").fetchone()
    chost = chost_row[0] if chost_row else "crafty"
    old_bk = f"{chost}:{server_data.get('server_port')}"
    new_bk = f"{chost}:{port}"
    routes_updated = 0
    with get_db() as con:
        impacted = con.execute(
            "SELECT id, hostname, is_default FROM routes WHERE backend=?", (old_bk,)
        ).fetchall()
        for r in impacted:
            con.execute(
                "UPDATE routes SET backend=? WHERE id=?",
                (new_bk, r["id"]),
            )
            routes_updated += 1
            if r["is_default"]:
                err2 = await mc_router.push_default(new_bk)
            else:
                err2 = await mc_router.push_route(r["hostname"], new_bk)
            if err2:
                logger.warning("mc-router sync failed for route %d after port change: %s", r["id"], err2)
        con.commit()

    # 7) Restart server if requested
    if restart:
        logger.info("Restarting server %s", server_id)
        await asyncio.sleep(1)
        await crafty.crafty_request("post", f"/servers/{server_id}/action/start_server")

    if routes_updated:
        await broadcast("route-change", {"action": "edit", "reason": "crafty_port_change"})

    logger.info("Port change completed: server_id=%s port=%d (%d routes updated)", server_id, port, routes_updated)

    return JSONResponse({
        "success": True,
        "message": f"Port changed to {port}. " + ("Server restarted." if restart else "Restart server to apply.") + (f" {routes_updated} route(s) updated in mc-router." if routes_updated else ""),
        "file_updated": file_updated,
        "file_path": str(prop_path) if prop_path else None,
        "api_updated": api_updated,
        "routes_updated": routes_updated,
    })
