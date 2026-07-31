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
# A set (instead of a list) makes unsubscribe idempotent and O(1); stale
# entries are pruned whenever broadcast() or unsubscribe() runs.
_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    _subscribers.discard(q)


# Cached last-known state so a freshly connected client immediately receives a
# snapshot instead of waiting for the next change (now that the emitter loop
# only broadcasts when data actually changes).
_last_connections: dict = {}
_last_router_status: dict = {}


def snapshot() -> list[str]:
    """Return the cached state as ready-to-send SSE frames."""
    frames = []
    if _last_connections:
        frames.append(f"event: connections\ndata: {json.dumps(_last_connections)}\n\n")
    if _last_router_status:
        frames.append(f"event: router-status\ndata: {json.dumps(_last_router_status)}\n\n")
    return frames


async def broadcast(event: str, data: Any):
    payload = json.dumps(data)

    # Only emit periodic status payloads when they actually changed. Event
    # types like "route-change" must always be delivered, so they bypass this.
    if event == "connections":
        if data == _last_connections:
            return
        _last_connections = data
    elif event == "router-status":
        if data == _last_router_status:
            return
        _last_router_status = data

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
    """Publish connection and router status updates to connected clients.

    Data is only broadcast when it changes, which also avoids re-broadcasting
    stale state every cycle and reduces load on the mc-router API.
    """
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
