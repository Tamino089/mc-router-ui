"""
In-memory, asyncio-safe sliding-window rate limiter.

Sufficient for this single-process service. A multi-worker deployment would need
a shared store instead. All mutations are guarded by a lock so the attempt map
stays consistent across concurrent handlers.
"""

import asyncio
import time


class RateLimiter:
    """Sliding-window attempt counter keyed by arbitrary strings (IPs, user ids)."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: float,
        max_entries: int = 10000,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self._attempts: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def is_limited(self, key: str) -> bool:
        """Return True if the key exceeded max_attempts within the window."""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            attempts = [t for t in self._attempts.get(key, []) if t >= cutoff]
            if attempts:
                self._attempts[key] = attempts
            elif key in self._attempts:
                del self._attempts[key]

            # Bound memory growth from keys that never return.
            if len(self._attempts) > self.max_entries:
                for k in list(self._attempts):
                    self._attempts[k] = [
                        t for t in self._attempts[k] if t >= cutoff
                    ]
                    if not self._attempts[k]:
                        del self._attempts[k]
            return len(attempts) >= self.max_attempts

    async def record(self, key: str) -> None:
        """Register one attempt for the given key."""
        async with self._lock:
            self._attempts.setdefault(key, []).append(time.monotonic())

