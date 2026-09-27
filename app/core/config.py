import logging
import os
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", "/data/mcrouter-ui.db"))

ADMIN_USER = os.getenv("ADMIN_USERNAME", "").strip() or "admin"
DEFAULT_ADMIN_PASSWORD = "changeme"
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "").strip() or DEFAULT_ADMIN_PASSWORD

MC_ROUTER_API = os.getenv("MC_ROUTER_API", "http://localhost:8080")
MC_PORT = int(os.getenv("MC_PORT", "25565"))
API_PORT = int(os.getenv("API_PORT", "8080"))

SECRET_KEY_ENV = os.getenv("SECRET_KEY", "")
SECRET_KEY: str = ""

SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_SAME_SITE = os.getenv("SESSION_SAME_SITE", "lax").strip().lower()
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(14 * 24 * 60 * 60)))

CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CF_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "")
CF_ZONE_NAME = os.getenv("CLOUDFLARE_ZONE_NAME", "")
DDNS_INTERVAL = int(os.getenv("DDNS_INTERVAL_SECONDS", "300"))
CF_API_BASE = "https://api.cloudflare.com/client/v4"
CF_ENABLED = bool(CF_API_TOKEN and (CF_ZONE_ID or CF_ZONE_NAME))

CRAFTY_URL_ENV = os.getenv("CRAFTY_URL", "")
CRAFTY_API_KEY_ENV = os.getenv("CRAFTY_API_KEY", "")
CRAFTY_CONTAINER_HOST_ENV = os.getenv("CRAFTY_CONTAINER_HOST", "")

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")

def docker_enabled() -> bool:
    return os.path.exists(DOCKER_SOCKET)

HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))
HEALTH_HISTORY_RETENTION_HOURS = int(os.getenv("HEALTH_HISTORY_RETENTION_HOURS", "24"))

_VALID_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")

def log_level() -> int:
    raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if raw in _VALID_LOG_LEVELS:
        return getattr(logging, raw)
    logging.getLogger(__name__).warning(
        "Invalid LOG_LEVEL %r; falling back to INFO (expected one of %s)",
        raw,
        ", ".join(_VALID_LOG_LEVELS),
    )
    return logging.INFO

ALL_PERMISSIONS = [
    "see_own_routes",
    "see_all_routes",
    "create_route",
    "edit_own_route",
    "delete_own_route",
    "manage_default_route",
    "see_cloudflare",
    "manage_cloudflare",
    "see_servers",
    "manage_servers",
    "see_all_users",
    "manage_users",
    "manage_settings",
]

DEFAULT_USER_PERMISSIONS = [
    "see_own_routes",
    "create_route",
    "edit_own_route",
    "delete_own_route",
]

MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", "12"))
