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


def tcp_check(host: str, port: int, timeout: float = 3.0) -> tuple[bool, float, str]:
    """
    TCP connectivity check. Returns (healthy, latency_ms, error_reason).
    Latency is -1 and error_reason is set if unreachable.

    Distinguishing these cases matters: "DNS lookup failed" almost always
    means the backend uses a Docker container hostname that this container
    can't resolve (different Docker network), while "timeout" / "connection
    refused" point at the target server itself being down or firewalled.
    """
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = (time.monotonic() - start) * 1000
            return True, round(elapsed, 1), ""
    except socket.gaierror as e:
        return False, -1, f"DNS lookup failed for '{host}': {e}"
    except socket.timeout:
        return False, -1, f"Timed out connecting to {host}:{port} after {timeout}s"
    except ConnectionRefusedError:
        return False, -1, f"Connection refused by {host}:{port}"
    except OSError as e:
        return False, -1, f"{host}:{port} unreachable: {e}"
    except Exception as e:
        return False, -1, f"{host}:{port} check failed: {e}"


async def check_all_routes():
    """Run TCP health checks on all route backends and store results."""
    with get_db() as con:
        routes = con.execute("SELECT id, backend FROM routes").fetchall()

    if not routes:
        return

    loop = asyncio.get_running_loop()

    async def check_one(route):
        parts = route["backend"].rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 25565
        healthy, latency, error = await loop.run_in_executor(
            None, tcp_check, host, port
        )
        return route["id"], healthy, latency, error

    results = await asyncio.gather(*[check_one(r) for r in routes], return_exceptions=True)

    with get_db() as con:
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Health check failed with exception: %s", result)
                continue
            route_id, healthy, latency, error = result
            con.execute(
                """INSERT INTO health_checks (route_id, healthy, latency_ms, checked_at, error)
                   VALUES (?, ?, ?, datetime('now'), ?)
                   ON CONFLICT(route_id) DO UPDATE SET
                       healthy=excluded.healthy,
                       latency_ms=excluded.latency_ms,
                       checked_at=excluded.checked_at,
                       error=excluded.error""",
                (route_id, int(healthy), latency if healthy else None, error or None),
            )
            con.execute(
                """INSERT INTO health_history (route_id, healthy, latency_ms, checked_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (route_id, int(healthy), latency if healthy else None),
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
    consecutive_errors = 0
    while True:
        try:
            await check_all_routes()
            await prune_old_history()
            consecutive_errors = 0
        except Exception:
            logger.exception("Error in health check loop")
            consecutive_errors += 1
        backoff = min(consecutive_errors * HEALTH_CHECK_INTERVAL, 600)
        await asyncio.sleep(HEALTH_CHECK_INTERVAL + backoff)
