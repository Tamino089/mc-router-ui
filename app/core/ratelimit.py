import asyncio
import time


class RateLimiter:

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
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            attempts = [t for t in self._attempts.get(key, []) if t >= cutoff]
            if attempts:
                self._attempts[key] = attempts
            elif key in self._attempts:
                del self._attempts[key]

            if len(self._attempts) > self.max_entries:
                for k in list(self._attempts):
                    self._attempts[k] = [
                        t for t in self._attempts[k] if t >= cutoff
                    ]
                    if not self._attempts[k]:
                        del self._attempts[k]
            return len(attempts) >= self.max_attempts

    async def record(self, key: str) -> None:
        async with self._lock:
            self._attempts.setdefault(key, []).append(time.monotonic())
