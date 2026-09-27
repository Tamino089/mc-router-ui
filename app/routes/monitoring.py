import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import crafty, mc_router
from app.services.health import tcp_check

router = APIRouter()

def _as_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None

def _visible_route(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return None, JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    with get_db() as con:
        route = con.execute(
            "SELECT id, backend, owner_id FROM routes WHERE id=?",
            (route_id,),
        ).fetchone()

    if not route:
        return None, JSONResponse({"success": False, "error": "Route not found"}, status_code=404)
    perms = user_has_perm_set(user)
    if (
        user.get("role") != "admin"
        and "see_all_routes" not in perms
        and ("see_own_routes" not in perms or route["owner_id"] != user["id"])
    ):
        return None, JSONResponse({"success": False, "error": "Forbidden"}, status_code=403)

    return route, None

def user_has_perm_set(user: dict) -> set:
    from app.db.schema import get_user_perms

    return get_user_perms(user["id"])

@router.get("/api/health/{route_id}")
async def check_route_health(request: Request, route_id: int):
    r_row, error = _visible_route(request, route_id)
    if error:
        return error
    backend = r_row["backend"]

    parts = backend.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        host, port = parts[0], int(parts[1])
    else:
        host, port = backend, 25565

    healthy, latency, error = await asyncio.to_thread(tcp_check, host, port)

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
        con.execute(
            """INSERT INTO health_history (route_id, healthy, latency_ms, checked_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (route_id, int(healthy), latency if healthy else None),
        )
        con.commit()

    return {"healthy": healthy, "latency_ms": latency, "error": error}

@router.get("/api/router-status")
async def check_router_status(request: Request):
    if not current_user(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    _, err = await mc_router.router_request("get", "/routes")
    return {"online": err is None, "error": err}

@router.get("/api/connections")
async def get_all_connections(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
    conns = await mc_router.get_connections()
    perms = user_has_perm_set(user)
    if user.get("role") == "admin" or "see_all_routes" in perms:
        return conns
    if "see_own_routes" not in perms:
        return {}

    with get_db() as con:
        visible = {
            row["hostname"]
            for row in con.execute(
                "SELECT hostname FROM routes WHERE owner_id=?",
                (user["id"],),
            ).fetchall()
        }
    return {hostname: count for hostname, count in conns.items() if hostname in visible}

@router.get("/api/ports/used")
async def get_used_ports(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    perms = user_has_perm_set(user)
    used = []
    with get_db() as con:
        if user.get("role") == "admin" or "see_all_routes" in perms:
            rows = con.execute("SELECT backend FROM routes").fetchall()
        elif "see_own_routes" in perms:
            rows = con.execute(
                "SELECT backend FROM routes WHERE owner_id=?",
                (user["id"],),
            ).fetchall()
        else:
            rows = []
        for r in rows:
            parts = r["backend"].rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                used.append(int(parts[1]))

    if user.get("role") == "admin" or user_has_perm(user, "see_servers"):
        data, err = await crafty.crafty_request("get", "/servers")
        if not err and isinstance(data, list):
            for s in data:
                port = _as_port(s.get("server_port") if isinstance(s, dict) else None)
                if port is not None:
                    used.append(port)

    return {"success": True, "used_ports": list(set(used))}

@router.get("/api/health/{route_id}/history")
async def get_route_health_history(request: Request, route_id: int):
    _, error = _visible_route(request, route_id)
    if error:
        return error

    with get_db() as con:

        rows = con.execute(
            """SELECT healthy, latency_ms, checked_at
               FROM health_history
               WHERE route_id=?
               ORDER BY id DESC LIMIT 60""",
            (route_id,),
        ).fetchall()

    return {"success": True, "history": [dict(r) for r in reversed(rows)]}
