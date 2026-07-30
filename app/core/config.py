"""
Centralized configuration — all environment variables and constants.
"""

import os
from pathlib import Path

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = Path(os.getenv("DB_PATH", "/data/mcrouter-ui.db"))

# ── Admin credentials ─────────────────────────────────────────────────────────
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "changeme")

# ── mc-router API ─────────────────────────────────────────────────────────────
MC_ROUTER_API = os.getenv("MC_ROUTER_API", "http://localhost:8080")
MC_PORT = int(os.getenv("MC_PORT", "25565"))
API_PORT = int(os.getenv("API_PORT", "8080"))

# ── Session key ───────────────────────────────────────────────────────────────
SECRET_KEY_ENV = os.getenv("SECRET_KEY", "")
SECRET_KEY: str = ""  # Resolved in schema.init_db()

# ── Cloudflare DDNS ───────────────────────────────────────────────────────────
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CF_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "")
CF_ZONE_NAME = os.getenv("CLOUDFLARE_ZONE_NAME", "")
DDNS_INTERVAL = int(os.getenv("DDNS_INTERVAL_SECONDS", "300"))
CF_API_BASE = "https://api.cloudflare.com/client/v4"
CF_ENABLED = bool(CF_API_TOKEN and (CF_ZONE_ID or CF_ZONE_NAME))

# ── Crafty Controller ─────────────────────────────────────────────────────────
CRAFTY_URL_ENV = os.getenv("CRAFTY_URL", "")
CRAFTY_API_KEY_ENV = os.getenv("CRAFTY_API_KEY", "")

# ── Docker socket ──────────────────────────────────────────────────────────────
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")


def docker_enabled() -> bool:
    """Check if Docker socket is available at call time (not import time)."""
    return os.path.exists(DOCKER_SOCKET)

# ── Health checks ─────────────────────────────────────────────────────────────
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))
HEALTH_HISTORY_RETENTION_HOURS = int(os.getenv("HEALTH_HISTORY_RETENTION_HOURS", "24"))

# ── Permissions ───────────────────────────────────────────────────────────────
ALL_PERMISSIONS = [
    "see_own_routes",
    "see_all_routes",
    "create_route",
    "edit_own_route",
    "delete_own_route",
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
