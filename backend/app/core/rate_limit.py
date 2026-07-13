from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from threading import Lock


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> int | None:
        with self._lock:
            now = self._clock()
            events = self._events.get(key)
            if events is None:
                return None
            self._discard_expired(events, now)
            if not events:
                self._events.pop(key, None)
                return None
            if len(events) >= self.limit:
                return self._retry_after(events, now)
            return None

    def consume(self, key: str) -> int | None:
        with self._lock:
            now = self._clock()
            events = self._events.get(key)
            if events is None:
                self._make_room(now)
                events = self._events.setdefault(key, deque())
            self._discard_expired(events, now)
            if len(events) >= self.limit:
                return self._retry_after(events, now)
            events.append(now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def _discard_expired(self, events: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()

    def _retry_after(self, events: deque[float], now: float) -> int:
        return max(1, math.ceil(events[0] + self.window_seconds - now))

    def _make_room(self, now: float) -> None:
        if len(self._events) < self.max_keys:
            return
        for key, events in list(self._events.items()):
            self._discard_expired(events, now)
            if not events:
                self._events.pop(key, None)
        if len(self._events) >= self.max_keys:
            oldest_key = min(self._events, key=lambda key: self._events[key][0])
            self._events.pop(oldest_key, None)
