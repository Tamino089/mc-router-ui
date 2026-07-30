"""
Server-Sent Events (SSE) engine — pushes real-time updates to connected clients.

Replaces REST polling for health status, connection counts, and route changes.
"""

import asyncio
import json
import logging
from typing import Any

from app.services import mc_router

logger = logging.getLogger(__name__)

# ── In-memory subscriber management ──────────────────────────────────────────
_subscribers: list[asyncio.Queue] = []


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
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
    """Publish connection and router status updates to connected clients."""
    consecutive_errors = 0
    while True:
        try:
            conns = await mc_router.get_connections()
            await broadcast("connections", conns)

            _, err = await mc_router.router_request("get", "/routes")
            await broadcast("router-status", {"online": err is None, "error": err})

            consecutive_errors = 0
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in SSE emitter loop")
            consecutive_errors += 1
        backoff = min(consecutive_errors * 10, 300)
        await asyncio.sleep(10 + backoff)