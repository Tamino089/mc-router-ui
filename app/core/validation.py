"""
Shared validation patterns for hostnames, backends, and IP:PORT addresses.

Kept in its own module so route handlers and API endpoints use identical
rules instead of re-importing from ``app.routes.routes`` (which caused a
module importing itself).
"""

import re

# ── Strict validation patterns ────────────────────────────────────────────────
HOSTNAME_RE = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
)
SOCKET_ADDR_RE = re.compile(
    r'^[a-zA-Z0-9._-]+:[0-9]{1,5}$'
)
IP_PORT_RE = re.compile(
    r'^(\d{1,3}\.){3}\d{1,3}:\d{1,5}$'
)
BARE_ADDR_RE = re.compile(r'^[a-zA-Z0-9._-]+$')

# mc-router and the health checker both default to 25565 when no port is given.
DEFAULT_MINECRAFT_PORT = 25565


def valid_ip_port(s: str) -> bool:
    """Validate IP:PORT with proper octet ranges (0-255 each)."""
    m = IP_PORT_RE.match(s)
    if not m:
        return False
    ip_part, port_part = s.rsplit(":", 1)
    try:
        octets = [int(o) for o in ip_part.split(".")]
        if any(o < 0 or o > 255 for o in octets):
            return False
        port = int(port_part)
        return 1 <= port <= 65535
    except ValueError:
        return False


def is_valid_backend(backend: str) -> bool:
    """Accept HOST:PORT, IP:PORT, or a bare hostname/IP (port defaults later)."""
    if not backend:
        return False
    if SOCKET_ADDR_RE.match(backend) or valid_ip_port(backend):
        return True
    return bool(BARE_ADDR_RE.match(backend))


def normalize_backend(backend: str) -> str:
    """Return a backend with an explicit port; bare addresses get :25565."""
    if not backend:
        return backend
    if ":" in backend:
        return backend
    return f"{backend}:{DEFAULT_MINECRAFT_PORT}"


def parse_backend(backend: str) -> tuple[str, int]:
    """Split a backend into (host, port), defaulting the port to 25565."""
    parts = backend.rsplit(":", 1)
    host = parts[0]
    port = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else DEFAULT_MINECRAFT_PORT
    return host, port
