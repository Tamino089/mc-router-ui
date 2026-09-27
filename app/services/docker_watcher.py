"""
Docker socket integration that discovers routes from container labels.

Reads mc-router labels from running containers and returns them as read-only
route sources. Only active when the configured Docker socket exists.
"""

import asyncio
import logging

import httpx

from app.core.config import DOCKER_SOCKET, docker_enabled

logger = logging.getLogger(__name__)

DOCKER_LABEL_HOST = "mc-router.host"
DOCKER_LABEL_EXTERNAL_SERVER = "mc-router.itzg.me/externalServerName"

_cache: list[dict] = []
_cache_valid = False
_cache_ts: float = 0.0
_CACHE_TTL = 10.0


async def _docker_request(method: str, path: str, **kwargs):
    client = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=str(DOCKER_SOCKET)), timeout=5
    )
    try:
        response = await getattr(client, method)(f"http://localhost{path}", **kwargs)
        response.raise_for_status()
        return response.json(), None
    except Exception as e:
        return None, str(e)
    finally:
        await client.aclose()


async def discover_docker_routes(force: bool = False) -> list[dict]:
    """Return route dicts discovered from container labels.

    Each entry has hostname, backend, source='docker', running, container_name.
    """
    global _cache, _cache_valid, _cache_ts

    now = asyncio.get_running_loop().time()
    if not force and _cache_valid and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    if not docker_enabled():
        _cache, _cache_valid, _cache_ts = [], True, now
        return _cache

    data, err = await _docker_request("get", "/containers/json")
    if err or not data:
        logger.warning("Docker socket unavailable: %s", err)
        return []

    discovered = []
    for container in data:
        names = container.get("Names", [])
        labels = container.get("Labels", {}) or {}
        ports = container.get("Ports", [])
        container_name = (names[0] if names else "unknown").lstrip("/")

        hostname = (
            labels.get(DOCKER_LABEL_EXTERNAL_SERVER)
            or labels.get(DOCKER_LABEL_HOST)
            or ""
        ).strip().lower()
        if not hostname:
            continue

        # First published TCP port wins; mc-router reaches it through the host.
        backend = ""
        for port in ports:
            private_port = port.get("PrivatePort")
            if private_port and port.get("Type") == "tcp":
                backend = f"{port.get('IP', '127.0.0.1')}:{private_port}"
                break

        if not backend:
            # No published port, so rely on the container name resolving on a
            # shared Docker network.
            backend = f"{container_name}:25565"
            logger.warning(
                "Container %s exposes no TCP port; falling back to backend %s",
                container_name,
                backend,
            )

        discovered.append(
            {
                "hostname": hostname,
                "backend": backend,
                "source": "docker",
                "running": container.get("State", "") == "running",
                "container_name": container_name,
            }
        )

    # An empty result is cached too, so a Docker-less or failing host does not
    # trigger a fresh socket round trip on every request.
    _cache, _cache_valid, _cache_ts = discovered, True, now
    return discovered


async def is_docker_managed(hostname: str) -> bool:
    routes = await discover_docker_routes()
    return any(r["hostname"] == hostname for r in routes)


async def docker_watcher_loop() -> None:
    """Keep the Docker route cache fresh."""
    while True:
        try:
            await discover_docker_routes(force=True)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in Docker watcher loop")
        await asyncio.sleep(_CACHE_TTL)
