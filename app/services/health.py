"""
Background health worker that probes route backends and records latency history.
"""

import asyncio
import logging
import socket
import time
from datetime import UTC, datetime, timedelta

from app.core.config import HEALTH_CHECK_INTERVAL, HEALTH_HISTORY_RETENTION_HOURS
from app.core.validation import parse_backend
from app.db.database import get_db

logger = logging.getLogger(__name__)

# Bounds concurrent probes so a large route table cannot exhaust the executor.
_MAX_CONCURRENT_CHECKS = 32


def tcp_check(host: str, port: int, timeout: float = 3.0) -> tuple[bool, float, str]:
    """Probe TCP connectivity. Returns (healthy, latency_ms, error_reason).

    The failure kinds are kept distinct because they point at different fixes: a
    DNS failure usually means the backend hostname is not resolvable from this
    container's network, while a timeout or refusal means the target itself is
    down or filtered.
    """
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.monotonic() - start) * 1000, 1), ""
    except socket.gaierror as e:
        return False, -1, f"DNS lookup failed for '{host}': {e}"
    except TimeoutError:
        return False, -1, f"Timed out connecting to {host}:{port} after {timeout}s"
    except ConnectionRefusedError:
        return False, -1, f"Connection refused by {host}:{port}"
    except OSError as e:
        return False, -1, f"{host}:{port} unreachable: {e}"
    except Exception as e:
        return False, -1, f"{host}:{port} check failed: {e}"


async def check_all_routes() -> None:
    """Probe every route backend and store the results."""
    with get_db() as con:
        routes = con.execute("SELECT id, backend FROM routes").fetchall()

    if not routes:
        return

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

    async def check_one(route):
        async with semaphore:
            host, port = parse_backend(route["backend"])
            healthy, latency, error = await asyncio.to_thread(tcp_check, host, port)
        return route["id"], healthy, latency, error

    results = await asyncio.gather(
        *(check_one(route) for route in routes), return_exceptions=True
    )

    with get_db() as con:
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Health check raised: %s", result)
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


async def prune_old_history() -> None:
    """Delete health history outside the retention window."""
    cutoff = datetime.now(UTC) - timedelta(
        hours=HEALTH_HISTORY_RETENTION_HOURS
    )
    with get_db() as con:
        con.execute(
            "DELETE FROM health_history WHERE checked_at < ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        con.commit()


async def health_loop() -> None:
    """Probe all routes every HEALTH_CHECK_INTERVAL seconds."""
    consecutive_errors = 0
    while True:
        try:
            await check_all_routes()
            consecutive_errors = 0
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in health check loop")
            consecutive_errors += 1

        # Pruning is independent, so a failing check pass cannot stall it and
        # let history grow without bound.
        try:
            await prune_old_history()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error pruning health history")

        backoff = min(consecutive_errors * HEALTH_CHECK_INTERVAL, 600)
        await asyncio.sleep(HEALTH_CHECK_INTERVAL + backoff)
