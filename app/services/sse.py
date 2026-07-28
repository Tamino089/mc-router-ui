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
_subscriber_counter = 0
subscribers: list[tuple[int, asyncio.Queue]] = []


def subscribe() -> asyncio.Queue:
    global _subscriber_counter
    _subscriber_counter += 1
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    subscribers.append((_subscriber_counter, q))
    return q


def unsubscribe(q: asyncio.Queue):
    global subscribers
    subscribers = [(sid, sq) for sid, sq in subscribers if sq is not q]


async def broadcast(event: str, data: Any):
    payload = json.dumps(data)
    dead: list[int] = []
    for sid, q in subscribers:
        try:
            q.put_nowait(f"event: {event}\ndata: {payload}\n\n")
        except asyncio.QueueFull:
            logger.warning(
                "SSE subscriber %d queue full (maxsize=100) — dropping and removing",
                sid,
            )
            dead.append(sid)
    if dead:
        subscribers[:] = [(sid, q) for sid, q in subscribers if sid not in dead]


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