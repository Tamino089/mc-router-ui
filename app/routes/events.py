"""
SSE endpoint for real-time streaming of health, connections, and route changes.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.security import current_user
from app.services.sse import subscribe, unsubscribe

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/events")
async def sse_stream(request: Request):
    user = current_user(request)
    if not user:
        from fastapi.responses import JSONResponse
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    queue = subscribe()

    async def event_generator():
        try:
            # Send initial keepalive
            yield "event: connected\ndata: {}\n\n"
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