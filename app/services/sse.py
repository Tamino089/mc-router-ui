"""
Server-Sent Events engine for live health, connection, and route updates.
"""

import asyncio
import json
import logging
from typing import Any

from app.services import mc_router

logger = logging.getLogger(__name__)

# Subscriber queue mapped to the hostnames that subscriber may see. None means
# unrestricted (admin or see_all_routes). Carrying visibility per subscriber
# lets broadcast() filter the shared connection map instead of leaking every
# hostname to every connected user.
_subscribers: dict[asyncio.Queue, frozenset[str] | None] = {}

# Last payload per event type, so a new client gets an immediate snapshot and
# unchanged payloads are not re-sent.
_last_connections: dict = {}
_last_router_status: dict = {}


def subscribe(allowed_hostnames: frozenset[str] | None) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[q] = allowed_hostnames
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.pop(q, None)


def _visible(payload: dict, allowed: frozenset[str] | None) -> dict:
    if allowed is None:
        return payload
    return {k: v for k, v in payload.items() if k in allowed}


def snapshot(allowed: frozenset[str] | None) -> list[str]:
    """Return the cached state as ready-to-send SSE frames for one subscriber."""
    frames = []
    if _last_connections:
        conns = _visible(_last_connections, allowed)
        if conns:
            frames.append(f"event: connections\ndata: {json.dumps(conns)}\n\n")
    if _last_router_status:
        frames.append(
            f"event: router-status\ndata: {json.dumps(_last_router_status)}\n\n"
        )
    return frames


async def broadcast(event: str, data: Any) -> None:
    global _last_connections, _last_router_status

    # Periodic status payloads are sent only when they change; route-change
    # events are always delivered.
    if event == "connections":
        if data == _last_connections:
            return
        _last_connections = data
    elif event == "router-status":
        if data == _last_router_status:
            return
        _last_router_status = data

    dead = []
    for q, allowed in list(_subscribers.items()):
        if event == "connections":
            payload = json.dumps(_visible(data, allowed))
        else:
            payload = json.dumps(data)
        try:
            q.put_nowait(f"event: {event}\ndata: {payload}\n\n")
        except asyncio.QueueFull:
            # Client is too slow to keep up. Drop it rather than blocking the
            # emitter loop; it reconnects and receives a fresh snapshot.
            logger.warning("SSE subscriber queue full, dropping subscriber")
            dead.append(q)
    for q in dead:
        unsubscribe(q)


async def sse_emitter_loop() -> None:
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
