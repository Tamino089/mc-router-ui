"""
Server-Sent Events (SSE) engine — pushes real-time updates to connected clients.

Replaces REST polling for health status, connection counts, and route changes.
"""

import asyncio
import json
import logging
import time
from typing import Any

from app.services import mc_router
from app.services.health import check_all_routes

logger = logging.getLogger(__name__)

# ── In-memory subscriber management ──────────────────────────────────────────
_subscribers: list[asyncio.Queue] = []


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    if q in _subscribers:
        _subscribers.remove(q)


async def broadcast(event: str, data: Any):
    payload = json.dumps(data)
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(f"event: {event}\ndata: {payload}\n\n")
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        unsubscribe(q)


# ── Background emitter loop ──────────────────────────────────────────────────
async def sse_emitter_loop():
    """Periodically checks health + connections and broadcasts to subscribers."""
    while True:
        try:
            # 1) Health check all routes
            await check_all_routes()

            # 2) Fetch connections from mc-router
            conns = await mc_router.get_connections()
            await broadcast("connections", conns)

            # 3) Router status
            _, err = await mc_router.router_request("get", "/routes")
            await broadcast("router-status", {"online": err is None, "error": err})

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in SSE emitter loop")
        await asyncio.sleep(10)