"""
Health and monitoring endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services import mc_router
from app.services.health import tcp_check

router = APIRouter()


@router.get("/api/health/{route_id}")
async def check_route_health(request: Request, route_id: int):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    with get_db() as con:
        r_row = con.execute("SELECT backend FROM routes WHERE id=?", (route_id,)).fetchone()
        if not r_row:
            return JSONResponse({"error": "Route not found"}, status_code=404)

        backend = r_row["backend"]

    parts = backend.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        host, port = parts[0], int(parts[1])
    else:
        host, port = backend, 25565

    healthy, latency = tcp_check(host, port)
    
    # Update DB async (or synchronously, it's fast enough)
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
        con.execute(
            """INSERT INTO health_history (route_id, healthy, latency_ms, checked_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (route_id, int(healthy), latency if healthy else None),
        )
        con.commit()

    return {"healthy": healthy, "latency_ms": latency}


@router.get("/api/router-status")
async def check_router_status():
    """Check if the backend mc-router binary is answering."""
    _, err = await mc_router.router_request("get", "/routes")
    return {"online": err is None, "error": err}


@router.get("/api/connections")
async def get_all_connections():
    """Get active connections for all routes."""
    conns = await mc_router.get_connections()
    return conns


@router.get("/api/ports/used")
async def get_used_ports(request: Request):
    """Return all ports currently in use by routes or Crafty servers."""
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    used = []
    with get_db() as con:
        rows = con.execute("SELECT backend FROM routes").fetchall()
        for r in rows:
            parts = r["backend"].rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                used.append(int(parts[1]))

    # If the user has permission, add crafty ports
    if user.get("role") == "admin" or user_has_perm(user, "see_servers"):
        from app.services import crafty
        data, err = await crafty.crafty_request("get", "/servers")
        if not err and data:
            for s in data:
                p = s.get("server_port")
                if p:
                    used.append(int(p))

    return {"success": True, "used_ports": list(set(used))}

@router.get("/api/health/{route_id}/history")
async def get_route_health_history(request: Request, route_id: int):
    """Get the last 60 health check records (up to 30 mins) for a sparkline."""
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    with get_db() as con:
        # Get the last 60 records for this route, order chronologically
        rows = con.execute(
            """SELECT healthy, latency_ms, checked_at 
               FROM health_history 
               WHERE route_id=? 
               ORDER BY checked_at DESC LIMIT 60""",
            (route_id,)
        ).fetchall()
        
    # Reverse so oldest is first
    rows.reverse()
    
    return {
        "success": True, 
        "history": [dict(r) for r in rows]
    }
