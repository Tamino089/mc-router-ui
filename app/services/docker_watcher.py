"""
Docker socket integration — discovers mc-router routes from container labels.

Reads mc-router.host labels from running containers and returns them as
read-only route sources.  The Docker socket path is configured via the
DOCKER_SOCKET env-var and is only activated when the socket file exists.
"""

import asyncio
import json
import logging
import os
from typing import Optional

import httpx

from app.core.config import DOCKER_ENABLED, DOCKER_SOCKET

logger = logging.getLogger(__name__)

DOCKER_LABEL_HOST = "mc-router.host"
DOCKER_LABEL_EXTERNAL_SERVER = "mc-router.itzg.me/externalServerName"

_RECENTLY_SEEN: set[str] = set()
_cache: list[dict] = []
_cache_ts: float = 0
_CACHE_TTL = 10.0


async def _docker_request(method: str, path: str, **kwargs):
    uds = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(DOCKER_SOCKET)), timeout=5)
    try:
        r = await getattr(uds, method)(f"http://localhost{path}", **kwargs)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)
    finally:
        await uds.aclose()


async def discover_docker_routes(force: bool = False) -> list[dict]:
    """Query Docker for containers carrying mc-router labels.

    Returns a list of route dicts with keys:
      hostname, backend, source='docker', running, container_name
    """
    global _cache, _cache_ts
    now = asyncio.get_event_loop().time()
    if not force and _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    if not DOCKER_ENABLED:
        _cache = []
        return _cache

    data, err = await _docker_request("get", "/containers/json")
    if err or not data:
        logger.warning("Docker socket unavailable: %s", err)
        return []

    discovered = []
    global _RECENTLY_SEEN
    currently_seen = set()

    for c in data:
        names = c.get("Names", [])
        labels = c.get("Labels", {}) or {}
        state = c.get("State", "")
        ports = c.get("Ports", [])
        container_name = (names[0] if names else "unknown").lstrip("/")

        hostname = labels.get(DOCKER_LABEL_EXTERNAL_SERVER) or labels.get(DOCKER_LABEL_HOST) or ""
        if not hostname:
            continue

        hostname = hostname.strip().lower()
        currently_seen.add(hostname)

        # Determine backend from port mapping (first Minecraft port)
        backend = ""
        for p in ports:
            private_port = p.get("PrivatePort")
            ip = p.get("IP", "127.0.0.1")
            if private_port and p.get("Type") == "tcp":
                backend = f"{ip}:{private_port}"
                break
        if not backend:
            for p in ports:
                private_port = p.get("PrivatePort")
                if private_port and p.get("Type") == "tcp":
                    backend = f"docker-host:{private_port}"
                    break

        if not backend:
            backend = f"{container_name}:25565"

        discovered.append({
            "hostname": hostname,
            "backend": backend,
            "source": "docker",
            "running": state == "running",
            "container_name": container_name,
        })

    _RECENTLY_SEEN = currently_seen
    _cache = discovered
    _cache_ts = now
    return discovered


async def is_docker_managed(hostname: str) -> bool:
    """Check if a hostname is managed by a Docker container label."""
    routes = await discover_docker_routes()
    return any(r["hostname"] == hostname for r in routes)


async def docker_watcher_loop():
    """Background loop that keeps the Docker route cache fresh."""
    while True:
        try:
            await discover_docker_routes(force=True)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in Docker watcher loop")
        await asyncio.sleep(_CACHE_TTL)