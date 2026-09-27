import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.core import config
from app.core.csrf import SameOriginCsrfMiddleware, SecurityHeadersMiddleware
from app.core.security import current_user
from app.db import schema
from app.db.database import get_db
from app.routes import (
    auth,
    cloudflare_api,
    crafty_api,
    events,
    get_flash,
    healthz,
    monitoring,
    routes,
    settings,
    users,
)
from app.services import cloudflare, docker_watcher, health, mc_router
from app.services.routes_data import build_routes_payload
from app.services.sse import sse_emitter_loop

logging.basicConfig(
    level=config.log_level(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

try:
    secret_key = schema.init_db()
except Exception:
    # Sessions are signed with a persistent key (env var or generated into the
    # database). Falling back to a hardcoded key would let anyone forge
    # sessions, so fail fast instead.
    logger.critical(
        "Failed to initialize database. Refusing to start without a persistent "
        "session key. Check that DB_PATH (%s) is writable.",
        config.DB_PATH,
    )
    raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MC Router UI")

    with get_db() as con:
        await mc_router.sync_routes_to_router(con)

    tasks = [
        asyncio.create_task(cloudflare.ddns_loop(), name="ddns"),
        asyncio.create_task(health.health_loop(), name="health"),
        asyncio.create_task(sse_emitter_loop(), name="sse-emitter"),
        asyncio.create_task(docker_watcher.docker_watcher_loop(), name="docker-watcher"),
    ]

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("MC Router UI shutdown complete")


app = FastAPI(title="MC Router UI", lifespan=lifespan)

# Middleware runs in reverse registration order, so SecurityHeadersMiddleware
# (registered last) is outermost and stamps headers on every response including
# those produced by the session and CSRF layers.
app.add_middleware(SameOriginCsrfMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=secret_key,
    session_cookie="mc_router_ui_session",
    max_age=config.SESSION_MAX_AGE,
    same_site=config.SESSION_SAME_SITE,
    https_only=config.SESSION_HTTPS_ONLY,
)
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(healthz.router)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(users.router)
app.include_router(settings.router)
app.include_router(cloudflare_api.router)
app.include_router(crafty_api.router)
app.include_router(monitoring.router)
app.include_router(events.router)


# HTTPException is handled separately so authentication and validation failures
# keep their 401/403/404 status instead of becoming a 500.
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal", "detail": None, "path": request.url.path},
    )


@app.get("/")
async def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    is_admin = user["role"] == "admin"
    user_perms = set(config.ALL_PERMISSIONS) if is_admin else schema.get_user_perms(user["id"])

    flash = get_flash(request)
    routes_data = await build_routes_payload(user, user_perms)

    with get_db() as con:
        c_url = con.execute("SELECT value FROM settings WHERE key='crafty_url'").fetchone()
        c_tok = con.execute("SELECT value FROM settings WHERE key='crafty_token'").fetchone()
        c_host = con.execute(
            "SELECT value FROM settings WHERE key='crafty_container_host'"
        ).fetchone()
        crafty_token_raw = c_tok[0] if c_tok else ""

        cf_t = con.execute("SELECT value FROM settings WHERE key='cf_api_token'").fetchone()
        cf_z = con.execute("SELECT value FROM settings WHERE key='cf_zone_id'").fetchone()
        cf_zn = con.execute("SELECT value FROM settings WHERE key='cf_zone_name'").fetchone()

        # Empty stored values fall through to the environment so a blank field
        # saved in Settings cannot shadow an env-configured deployment.
        cf_token = (cf_t[0] if cf_t else "") or config.CF_API_TOKEN
        cf_zid = (cf_z[0] if cf_z else "") or config.CF_ZONE_ID
        cf_zname = (cf_zn[0] if cf_zn else "") or config.CF_ZONE_NAME
        cf_enabled = bool(cf_token and (cf_zid or cf_zname))

        # Only handlers holding the matching manage_* permission receive the raw
        # secret for editing; everyone else gets a mask so the value never
        # reaches the page source.
        can_manage_cf = is_admin or "manage_cloudflare" in user_perms
        can_manage_crafty = is_admin or "manage_settings" in user_perms

        def _masked(secret: str) -> str:
            return "•" * 12 if secret else ""

        crafty_config = {
            "url": c_url[0] if c_url else "",
            "token": crafty_token_raw if can_manage_crafty else _masked(crafty_token_raw),
            "container_host": c_host[0] if c_host else "",
        }
        cf_config = {
            "token": cf_token if can_manage_cf else _masked(cf_token),
            "zone_id": cf_zid,
            "zone_name": cf_zname,
        }

        show_wizard = False
        if is_admin and not cf_token and not crafty_config["url"]:
            wizard_done = con.execute(
                "SELECT value FROM settings WHERE key='setup_wizard_done'"
            ).fetchone()
            show_wizard = not wizard_done

    total_connections = sum(r["active_connections"] for r in routes_data)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "current_user": user,
            "user_perms": user_perms,
            "flash": flash,
            "all_permissions": config.ALL_PERMISSIONS,
            "routes": routes_data,
            "total_connections": total_connections,
            "cf_enabled": cf_enabled,
            "cf_config": cf_config,
            "crafty_config": crafty_config,
            "mc_port": config.MC_PORT,
            "mc_router_api": config.MC_ROUTER_API,
            "show_wizard": show_wizard,
            "docker_enabled": config.docker_enabled(),
            "default_password_warning": is_admin and schema.is_default_admin_password(),
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
        },
    )
