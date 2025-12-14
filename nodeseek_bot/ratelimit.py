from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass


@dataclass
class RateLimitState:
    next_allowed_monotonic: float = 0.0


class MinIntervalLimiter:
    def __init__(self, min_interval_seconds: int, jitter_seconds: int) -> None:
        self._min_interval = float(max(0, min_interval_seconds))
        self._jitter = float(max(0, jitter_seconds))
        self._lock = asyncio.Lock()
        self._state = RateLimitState()

    def next_allowed_in_seconds(self) -> float:
        now = time.monotonic()
        return max(0.0, self._state.next_allowed_monotonic - now)

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_s = self._state.next_allowed_monotonic - now
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            jitter = random.uniform(0.0, self._jitter) if self._jitter else 0.0
            self._state.next_allowed_monotonic = time.monotonic() + self._min_interval + jitter
