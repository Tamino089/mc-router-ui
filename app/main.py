import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.core import config
from app.core.csrf import SameOriginCsrfMiddleware
from app.core.security import current_user
from app.db import schema
from app.db.database import get_db
from app.services import cloudflare, docker_watcher, health, mc_router
from app.services.sse import sse_emitter_loop

# Routers
from app.routes import auth, cloudflare_api, crafty_api, events, healthz, monitoring, routes, settings, users

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


secret_key = schema.init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Initializing MC Router UI...")
    
    # Sync initial state to mc-router
    with get_db() as con:
        await mc_router.sync_routes_to_router(con)
    
    # Start background loops
    ddns_task = asyncio.create_task(cloudflare.ddns_loop())
    health_task = asyncio.create_task(health.health_loop())
    sse_task = asyncio.create_task(sse_emitter_loop())
    docker_task = asyncio.create_task(docker_watcher.docker_watcher_loop())
    
    yield
    
    # Shutdown
    ddns_task.cancel()
    health_task.cancel()
    sse_task.cancel()
    docker_task.cancel()
    logger.info("MC Router UI shutdown complete.")


app = FastAPI(title="MC Router UI", lifespan=lifespan)
app.add_middleware(SameOriginCsrfMiddleware)
app.add_middleware(SessionMiddleware, secret_key=secret_key)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Include routers
app.include_router(healthz.router)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(users.router)
app.include_router(settings.router)
app.include_router(cloudflare_api.router)
app.include_router(crafty_api.router)
app.include_router(monitoring.router)
app.include_router(events.router)


# ── Global exception handler ────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled error on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal", "detail": None, "path": request.url.path},
    )


@app.get("/")
async def dashboard(request: Request):
    """Main dashboard render."""
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    user_perms = schema.get_user_perms(user["id"]) if user["role"] != "admin" else set(config.ALL_PERMISSIONS)

    with get_db() as con:
        # Load routes from DB (static sources)
        if user["role"] == "admin" or "see_all_routes" in user_perms:
            db_routes = con.execute(
                """SELECT r.*, u.username as owner_name, h.healthy, h.latency_ms
                   FROM routes r
                   LEFT JOIN users u ON r.owner_id = u.id
                   LEFT JOIN health_checks h ON r.id = h.route_id
                   ORDER BY r.is_default DESC, r.hostname ASC"""
            ).fetchall()
        else:
            db_routes = con.execute(
                """SELECT r.*, u.username as owner_name, h.healthy, h.latency_ms
                   FROM routes r
                   LEFT JOIN users u ON r.owner_id = u.id
                   LEFT JOIN health_checks h ON r.id = h.route_id
                   WHERE r.owner_id=?
                   ORDER BY r.is_default DESC, r.hostname ASC""",
                (user["id"],),
            ).fetchall()

        routes_data = [dict(r) for r in db_routes]

        # Merge Docker-discovered routes (read-only)
        docker_routes = await docker_watcher.discover_docker_routes()
        for dr in docker_routes:
            existing = next((r for r in routes_data if r["hostname"] == dr["hostname"]), None)
            if existing:
                existing["source"] = "docker"
                existing["container_name"] = dr["container_name"]
                existing["docker_running"] = dr["running"]
            else:
                routes_data.append({
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
                    "active_connections": 0,
                })

        # Enhance with active connections
        conns = await mc_router.get_connections()
        for r in routes_data:
            r["active_connections"] = conns.get(r["hostname"], 0)

        # Load crafty settings
        c_url = con.execute("SELECT value FROM settings WHERE key='crafty_url'").fetchone()
        c_tok = con.execute("SELECT value FROM settings WHERE key='crafty_token'").fetchone()
        c_host = con.execute("SELECT value FROM settings WHERE key='crafty_container_host'").fetchone()

        crafty_config = {
            "url": c_url[0] if c_url else "",
            "token": c_tok[0] if c_tok else "",
            "container_host": c_host[0] if c_host else "crafty",
        }

        # Load CF settings
        cf_t = con.execute("SELECT value FROM settings WHERE key='cf_api_token'").fetchone()
        cf_z = con.execute("SELECT value FROM settings WHERE key='cf_zone_id'").fetchone()
        cf_zn = con.execute("SELECT value FROM settings WHERE key='cf_zone_name'").fetchone()
        
        cf_token = cf_t[0] if cf_t else config.CF_API_TOKEN
        cf_zid = cf_z[0] if cf_z else config.CF_ZONE_ID
        cf_zname = cf_zn[0] if cf_zn else config.CF_ZONE_NAME
        cf_enabled = bool(cf_token and (cf_zid or cf_zname))

        cf_config = {
            "token": cf_token,
            "zone_id": cf_zid,
            "zone_name": cf_zname,
        }
        
        # Check if setup wizard is needed
        show_wizard = False
        if user["role"] == "admin" and not cf_token and not crafty_config["url"]:
            # If they haven't explicitly dismissed it (we can store a flag, but for now we just show it if nothing is configured)
            wizard_done = con.execute("SELECT value FROM settings WHERE key='setup_wizard_done'").fetchone()
            if not wizard_done:
                show_wizard = True

    total_connections = sum(conns.values())

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_user": user,
            "user_perms": user_perms,
            "all_permissions": config.ALL_PERMISSIONS,
            "routes": routes_data,
            "total_connections": total_connections,
            "cf_enabled": cf_enabled,
            "cf_config": cf_config,
            "crafty_config": crafty_config,
            "mc_port": config.MC_PORT,
            "mc_router_api": config.MC_ROUTER_API,
            "show_wizard": show_wizard,
            "docker_enabled": config.DOCKER_ENABLED,
        },
    )
