"""
SSE endpoint for streaming live health, connection, and route changes.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.security import current_user
from app.db.database import get_db
from app.db.schema import user_has_perm
from app.services.sse import snapshot, subscribe, unsubscribe

logger = logging.getLogger(__name__)

router = APIRouter()


def _allowed_hostnames(user: dict) -> frozenset[str] | None:
    """Hostnames this user may see, or None when unrestricted."""
    if user.get("role") == "admin" or user_has_perm(user, "see_all_routes"):
        return None
    if not user_has_perm(user, "see_own_routes"):
        return frozenset()
    with get_db() as con:
        rows = con.execute(
            "SELECT hostname FROM routes WHERE owner_id=?", (user["id"],)
        ).fetchall()
    return frozenset(r["hostname"] for r in rows)


@router.get("/api/events")
async def sse_stream(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    allowed = _allowed_hostnames(user)
    queue = subscribe(allowed)

    async def event_generator():
        try:
            yield "event: connected\ndata: {}\n\n"
            for frame in snapshot(allowed):
                yield frame
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
