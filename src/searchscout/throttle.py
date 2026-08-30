"""Request pacing.

The original slept 0.5s after every write. That is a fixed cost whether or not
the store is under pressure, and it does not bound a burst: two processes each
sleeping 0.5s still send twice the rate. A token bucket bounds the rate itself
and lets a short burst through without penalising the common case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate_per_second: float
    burst: int = 5
    _tokens: float = field(init=False, default=0.0)
    _last: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._tokens = float(self.burst)

    def acquire(self, *, sleep: bool = True) -> float:
        """Take one token, waiting if none is available. Returns seconds waited."""
        now = time.monotonic()
        self._tokens = min(
            float(self.burst), self._tokens + (now - self._last) * self.rate_per_second
        )
        self._last = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0

        wait = (1.0 - self._tokens) / self.rate_per_second
        if sleep:
            time.sleep(wait)
            self._last = time.monotonic()
            self._tokens = 0.0
        return wait
