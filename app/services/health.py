"""
Background health check worker — periodically probes all route backends
and stores latency history for sparkline rendering.
"""

import asyncio
import logging
import socket
import time
from datetime import datetime, timedelta

from app.core.config import HEALTH_CHECK_INTERVAL, HEALTH_HISTORY_RETENTION_HOURS
from app.db.database import get_db

logger = logging.getLogger(__name__)


def tcp_check(host: str, port: int, timeout: float = 2.0) -> tuple[bool, float]:
    """
    TCP connectivity check. Returns (healthy, latency_ms).
    Latency is -1 if unreachable.
    """
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = (time.monotonic() - start) * 1000
            return True, round(elapsed, 1)
    except Exception:
        return False, -1


async def check_all_routes():
    """Run TCP health checks on all route backends and store results."""
    with get_db() as con:
        routes = con.execute("SELECT id, backend FROM routes").fetchall()

    if not routes:
        return

    loop = asyncio.get_running_loop()

    for route in routes:
        parts = route["backend"].rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 25565

        healthy, latency = await loop.run_in_executor(
            None, tcp_check, host, port
        )

        with get_db() as con:
            # Upsert current health state
            con.execute(
                """INSERT INTO health_checks (route_id, healthy, latency_ms, checked_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(route_id) DO UPDATE SET
                       healthy=excluded.healthy,
                       latency_ms=excluded.latency_ms,
                       checked_at=excluded.checked_at""",
                (route["id"], int(healthy), latency if healthy else None),
            )
            # Append to history
            con.execute(
                """INSERT INTO health_history (route_id, healthy, latency_ms, checked_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (route["id"], int(healthy), latency if healthy else None),
            )
            con.commit()


async def prune_old_history():
    """Delete health history older than the retention window."""
    cutoff = datetime.utcnow() - timedelta(hours=HEALTH_HISTORY_RETENTION_HOURS)
    with get_db() as con:
        con.execute(
            "DELETE FROM health_history WHERE checked_at < ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        con.commit()


async def health_loop():
    """Background loop: check all routes every HEALTH_CHECK_INTERVAL seconds."""
    while True:
        try:
            await check_all_routes()
            await prune_old_history()
        except Exception:
            logger.exception("Error in health check loop")
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)
