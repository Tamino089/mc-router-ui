"""
Build the dashboard's routes payload: static routes from the DB, Docker
discovered routes merged in, and live connection counts from mc-router.
"""

from app.db.database import get_db
from app.services import docker_watcher, mc_router


async def build_routes_payload(user: dict, user_perms: set) -> list:
    """Return the permission-aware list of route dicts rendered by the dashboard."""
    with get_db() as con:
        if user["role"] == "admin" or "see_all_routes" in user_perms:
            db_routes = con.execute(
                """SELECT r.*, u.username as owner_name, h.healthy, h.latency_ms, h.error as health_error
                   FROM routes r
                   LEFT JOIN users u ON r.owner_id = u.id
                   LEFT JOIN health_checks h ON r.id = h.route_id
                   ORDER BY r.is_default DESC, r.hostname ASC"""
            ).fetchall()
        elif "see_own_routes" in user_perms:
            db_routes = con.execute(
                """SELECT r.*, u.username as owner_name, h.healthy, h.latency_ms, h.error as health_error
                   FROM routes r
                   LEFT JOIN users u ON r.owner_id = u.id
                   LEFT JOIN health_checks h ON r.id = h.route_id
                   WHERE r.owner_id=?
                   ORDER BY r.is_default DESC, r.hostname ASC""",
                (user["id"],),
            ).fetchall()
        else:
            db_routes = []

        routes_data = [dict(r) for r in db_routes]

        # Docker routes are not owned by an application user, so only users
        # allowed to see all routes may view this read-only source.
        can_see_docker_routes = (
            user["role"] == "admin" or "see_all_routes" in user_perms
        )
        docker_routes = (
            await docker_watcher.discover_docker_routes()
            if can_see_docker_routes
            else []
        )
        for dr in docker_routes:
            existing = next(
                (r for r in routes_data if r["hostname"] == dr["hostname"]), None
            )
            if existing:
                existing["source"] = "docker"
                existing["container_name"] = dr["container_name"]
                existing["docker_running"] = dr["running"]
            else:
                routes_data.append(
                    {
                        "id": None,
                        "hostname": dr["hostname"],
                        "backend": dr["backend"],
                        "is_default": 0,
                        "source": "docker",
                        "container_name": dr["container_name"],
                        "docker_running": dr["running"],
                        "owner_name": "Docker",
                        "owner_id": None,
                        "healthy": None,
                        "latency_ms": None,
                        "health_error": None,
                        "active_connections": 0,
                    }
                )

    # Enhance with active connections
    conns = await mc_router.get_connections()
    for r in routes_data:
        r["active_connections"] = conns.get(r["hostname"], 0)

    return routes_data
