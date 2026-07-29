"""
Async wrapper for communicating with the mc-router REST API.
"""

import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import MC_ROUTER_API

logger = logging.getLogger(__name__)

_RETRYABLE = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.TransportError)
_MAX_RETRIES = 3
_HEADERS = {"Connection": "close", "Content-Type": "application/json"}


async def router_request(method: str, path: str, **kwargs):
    """Generic mc-router API call with retry on transient errors.

    Returns (response, error_string).
    Uses Connection: close to prevent keep-alive socket resets.
    """
    url = MC_ROUTER_API.rstrip("/") + path
    last_err = None
    headers = {**_HEADERS, **(kwargs.pop("headers", {}))}
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await getattr(client, method)(url, headers=headers, **kwargs)
                if r.status_code >= 500:
                    last_err = f"mc-router API error {r.status_code}: {r.text}"
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(0.5 * attempt)
                        continue
                r.raise_for_status()
                return r, None
        except _RETRYABLE as e:
            last_err = e
            logger.warning("mc-router %s %s attempt %d/%d failed: %s", method.upper(), path, attempt, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(0.5 * attempt)
        except httpx.ConnectError:
            return None, f"mc-router unreachable ({MC_ROUTER_API})"
        except httpx.TimeoutException:
            return None, "mc-router not responding (timeout)"
        except httpx.HTTPStatusError as e:
            return None, f"mc-router rejected: {e.response.status_code} {e.response.text}"
        except Exception as e:
            return None, f"mc-router error: {e}"
    return None, f"mc-router connection lost after {_MAX_RETRIES} retries: {last_err}"


async def push_route(hostname: str, backend: str, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        _, err = await router_request("post", "/routes", json={"serverAddress": hostname, "backend": backend})
        if not err:
            return None
        if attempt < retries - 1:
            await asyncio.sleep(0.5 * (attempt + 1))
    return err


async def delete_route(hostname: str) -> Optional[str]:
    _, err = await router_request("delete", f"/routes/{hostname}")
    if err and "error 404" in err.lower():
        return None
    return err


async def push_default(backend: str, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        _, err = await router_request("post", "/defaultRoute", json={"backend": backend})
        if not err:
            return None
        if attempt < retries - 1:
            await asyncio.sleep(0.5 * (attempt + 1))
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