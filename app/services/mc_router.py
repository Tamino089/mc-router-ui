"""
Async client for the mc-router REST API.
"""

import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import MC_ROUTER_API

logger = logging.getLogger(__name__)

# httpx.TransportError already covers ConnectError, TimeoutException,
# RemoteProtocolError, ReadError, and WriteError.
_RETRYABLE = httpx.TransportError
_MAX_RETRIES = 3
_HEADERS = {"Connection": "close", "Content-Type": "application/json"}


def _describe(exc: BaseException) -> str:
    """Human-readable message for an exhausted transport error."""
    if isinstance(exc, httpx.ConnectError):
        return f"mc-router unreachable ({MC_ROUTER_API})"
    if isinstance(exc, httpx.TimeoutException):
        return "mc-router not responding (timeout)"
    return f"mc-router connection lost: {exc}"


async def router_request(method: str, path: str, retries: int = _MAX_RETRIES, **kwargs):
    """Call the mc-router API, retrying transient transport failures.

    Returns (response, error_string). Connection: close avoids keep-alive socket
    resets from the Go server.
    """
    url = MC_ROUTER_API.rstrip("/") + path
    headers = {**_HEADERS, **(kwargs.pop("headers", {}))}
    last_err: Optional[BaseException] = None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await getattr(client, method)(url, headers=headers, **kwargs)
                if r.status_code >= 500 and attempt < retries:
                    last_err = httpx.HTTPStatusError(
                        f"mc-router API error {r.status_code}",
                        request=r.request,
                        response=r,
                    )
                    await asyncio.sleep(0.5 * attempt)
                    continue
                r.raise_for_status()
                return r, None
        except _RETRYABLE as e:
            last_err = e
            logger.warning(
                "mc-router %s %s attempt %d/%d failed: %s",
                method.upper(),
                path,
                attempt,
                retries,
                e,
            )
            if attempt < retries:
                await asyncio.sleep(0.5 * attempt)
        except httpx.HTTPStatusError as e:
            return None, f"mc-router rejected: {e.response.status_code} {e.response.text}"
        except Exception as e:
            return None, f"mc-router error: {e}"

    if isinstance(last_err, httpx.HTTPStatusError):
        return None, f"mc-router API error {last_err.response.status_code}"
    return None, _describe(last_err) if last_err else "mc-router request failed"


async def push_route(
    hostname: str, backend: str, retries: int = _MAX_RETRIES
) -> Optional[str]:
    """Register a hostname route. Returns an error string or None on success."""
    err = None
    for attempt in range(retries):
        _, err = await router_request(
            "post",
            "/routes",
            retries=1,
            json={"serverAddress": hostname, "backend": backend},
        )
        if not err:
            return None
        if attempt < retries - 1:
            await asyncio.sleep(0.5 * (attempt + 1))
    return err


async def delete_route(hostname: str) -> Optional[str]:
    _, err = await router_request("delete", f"/routes/{hostname}")
    # A missing route already matches the desired end state.
    if err and "404" in err:
        return None
    return err


async def push_default(backend: str, retries: int = _MAX_RETRIES) -> Optional[str]:
    """Set the fallback backend used for unmatched hostnames."""
    err = None
    for attempt in range(retries):
        _, err = await router_request(
            "post", "/defaultRoute", retries=1, json={"backend": backend}
        )
        if not err:
            return None
        if attempt < retries - 1:
            await asyncio.sleep(0.5 * (attempt + 1))
    return err


async def clear_default() -> Optional[str]:
    """Best-effort removal of the fallback backend.

    mc-router exposes no DELETE for the default route, so an empty backend is
    sent instead. Returns an error string when the router would not confirm it.
    """
    _, err = await router_request(
        "post", "/defaultRoute", retries=1, json={"backend": ""}
    )
    return err


async def get_connections() -> dict:
    r, err = await router_request("get", "/connections")
    if err or not r:
        return {}
    try:
        data = r.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def sync_routes_to_router(db) -> None:
    """Push every stored route to mc-router.

    Uses single-attempt pushes so an unreachable router cannot stall startup for
    minutes; the periodic loops report ongoing failures afterwards.
    """
    rows = db.execute("SELECT hostname, backend, is_default FROM routes").fetchall()
    errors = 0
    for row in rows:
        if row[2]:
            err = await push_default(row[1], retries=1)
        else:
            err = await push_route(row[0], row[1], retries=1)
        if err:
            errors += 1
            logger.warning("Failed to sync route %s to mc-router: %s", row[0], err)
    if errors:
        logger.warning(
            "Synced %d route(s) to mc-router with %d error(s)", len(rows), errors
        )
    else:
        logger.info("Synced %d route(s) to mc-router", len(rows))
