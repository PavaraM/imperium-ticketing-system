import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class LoginRateLimiter:
    """In-memory sliding window of failed logins, keyed by IP + username digest.

    State is per process, so a restart clears it and multiple workers do not share it.
    That is an accepted trade-off for a single-container, ~200-guest event.
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return RateLimitResult(allowed=True)
            self._prune(hits, now)
            if not hits:
                self._hits.pop(key, None)
                return RateLimitResult(allowed=True)
            if len(hits) < self._max_attempts:
                return RateLimitResult(allowed=True)
            retry_after = int(self._window - (now - hits[0])) + 1
            return RateLimitResult(allowed=False, retry_after=max(1, retry_after))

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            hits.append(now)
            self._prune(hits, now)
            if len(self._hits) > 2048:
                self._drop_stale(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _prune(self, hits: deque[float], now: float) -> None:
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _drop_stale(self, now: float) -> None:
        cutoff = now - self._window
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            self._hits.pop(key, None)


def login_key(client_ip: str, username: str) -> str:
    digest = hashlib.sha256(username.strip().lower().encode()).hexdigest()[:16]
    return f"{client_ip}|{digest}"
