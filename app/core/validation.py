import re
from urllib.parse import urlsplit

HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)
SOCKET_ADDR_RE = re.compile(r"^[a-zA-Z0-9._-]+:[0-9]{1,5}$")
IP_PORT_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}:\d{1,5}$")
BARE_ADDR_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

DEFAULT_MINECRAFT_PORT = 25565

def valid_ip_port(value: str) -> bool:
    if not IP_PORT_RE.match(value):
        return False
    ip_part, port_part = value.rsplit(":", 1)
    try:
        octets = [int(o) for o in ip_part.split(".")]
        if any(o < 0 or o > 255 for o in octets):
            return False
        return 1 <= int(port_part) <= 65535
    except ValueError:
        return False

def is_valid_backend(backend: str) -> bool:
    if not backend:
        return False
    if SOCKET_ADDR_RE.match(backend) or valid_ip_port(backend):
        return True
    return bool(BARE_ADDR_RE.match(backend))

def normalize_backend(backend: str) -> str:
    if not backend:
        return backend
    if ":" in backend:
        return backend
    return f"{backend}:{DEFAULT_MINECRAFT_PORT}"

def parse_backend(backend: str) -> tuple[str, int]:
    parts = backend.rsplit(":", 1)
    host = parts[0]
    port = (
        int(parts[1])
        if len(parts) == 2 and parts[1].isdigit()
        else DEFAULT_MINECRAFT_PORT
    )
    return host, port

def normalize_base_url(url: str) -> str | None:
    candidate = (url or "").strip().rstrip("/")
    if not candidate:
        return None
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.hostname or parts.username or parts.password:
        return None
    return candidate
