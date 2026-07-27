"""
Async wrapper for communicating with the mc-router REST API.
"""

import logging
from typing import Optional

import httpx

from app.core.config import MC_ROUTER_API

logger = logging.getLogger(__name__)


async def router_request(method: str, path: str, **kwargs):
    """Generic mc-router API call. Returns (response, error_string)."""
    url = MC_ROUTER_API.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await getattr(client, method)(url, **kwargs)
            r.raise_for_status()
            return r, None
    except httpx.ConnectError:
        return None, f"Connection to mc-router ({MC_ROUTER_API}) failed"
    except httpx.TimeoutException:
        return None, "mc-router is not responding (timeout)"
    except httpx.HTTPStatusError as e:
        return None, f"mc-router API error {e.response.status_code}: {e.response.text}"
    except httpx.RemoteProtocolError as e:
        return None, f"mc-router connection lost: {e}"
    except httpx.ReadError as e:
        return None, f"mc-router read error: {e}"
    except httpx.WriteError as e:
        return None, f"mc-router write error: {e}"
    except httpx.TransportError as e:
        return None, f"mc-router transport error: {e}"
    except Exception as e:
        return None, f"mc-router error: {e}"


async def push_route(hostname: str, backend: str) -> Optional[str]:
    _, err = await router_request(
        "post", "/routes", json={"serverAddress": hostname, "backend": backend}
    )
    return err


async def delete_route(hostname: str) -> Optional[str]:
    _, err = await router_request("delete", f"/routes/{hostname}")
    if err and "error 404" in err.lower():
        return None
    return err


async def push_default(backend: str) -> Optional[str]:
    _, err = await router_request("post", "/defaultRoute", json={"backend": backend})
    return err


async def get_connections() -> dict:
    r, err = await router_request("get", "/connections")
    if err or not r:
        return {}
    try:
        return r.json()
    except Exception:
        return {}


async def sync_routes_to_router(db):
    """Push all stored routes to mc-router on startup."""
    rows = db.execute("SELECT hostname, backend, is_default FROM routes").fetchall()
    for row in rows:
        if row[2]:
            await push_default(row[1])
        else:
            await push_route(row[0], row[1])
    logger.info("Synced %d routes to mc-router", len(rows))