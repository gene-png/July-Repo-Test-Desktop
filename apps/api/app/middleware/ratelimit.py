"""Token-bucket rate limiting (Task S2-A H-2).

Implemented as FastAPI dependencies rather than middleware: dependencies compose
with the existing auth dependencies (so the per-user limiter can reuse
``current_user``) and are trivially unit-testable with a per-app bucket store.

Two limiter classes:
  * per-IP  - guards /auth/login, /auth/register, /auth/refresh.
  * per-user - guards the AI run endpoints (run-ai / generate / extract).

Backing store: Redis when ``settings.redis_url`` is set AND reachable at first
use, otherwise an in-memory dict (the test suite always lands here). The bucket
store lives on ``app.state`` so each app instance - including each per-test
TestClient - has its own isolated state.

Limits come from config (``shield_rate_limit_auth_per_min`` /
``shield_rate_limit_ai_per_min``); a limit of 0 disables that class. Over-limit
requests get a 429 with a ``Retry-After`` header (whole seconds) and a
structured log line.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings
from app.dependencies import current_user
from app.logging import get_logger
from app.models.user import User

_log = get_logger(__name__)

_WINDOW_SECONDS = 60.0


class InMemoryBucketStore:
    """Process-local token buckets. One entry per key: (tokens, last_refill)."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> tuple[bool, int]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        rate = limit / _WINDOW_SECONDS  # tokens per second
        with self._lock:
            tokens, last = self._buckets.get(key, (float(limit), now))
            tokens = min(float(limit), tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            retry = int(math.ceil((1.0 - tokens) / rate)) if rate > 0 else _WINDOW_SECONDS
            return False, max(1, retry)


class RedisBucketStore:
    """Redis-backed token bucket via a small atomic Lua script.

    Only used when a Redis connection is established at construction time; any
    import or connection failure makes the caller fall back to in-memory.
    """

    _LUA = """
    local tokens_key = KEYS[1]
    local ts_key = KEYS[2]
    local limit = tonumber(ARGV[1])
    local now = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])
    local rate = limit / window
    local tokens = tonumber(redis.call('get', tokens_key))
    local last = tonumber(redis.call('get', ts_key))
    if tokens == nil then tokens = limit; last = now end
    tokens = math.min(limit, tokens + (now - last) * rate)
    local allowed = 0
    local retry = 0
    if tokens >= 1 then
        tokens = tokens - 1
        allowed = 1
    else
        retry = math.ceil((1 - tokens) / rate)
    end
    redis.call('set', tokens_key, tokens, 'EX', math.ceil(window) * 2)
    redis.call('set', ts_key, now, 'EX', math.ceil(window) * 2)
    return {allowed, retry}
    """

    def __init__(self, client) -> None:  # noqa: ANN001 - redis client is duck-typed
        self._client = client
        self._script = client.register_script(self._LUA)

    def check(self, key: str, limit: int) -> tuple[bool, int]:
        now = time.time()
        allowed, retry = self._script(
            keys=[f"rl:{{{key}}}:t", f"rl:{{{key}}}:ts"],
            args=[limit, now, _WINDOW_SECONDS],
        )
        return bool(int(allowed)), max(1, int(retry)) if not int(allowed) else 0


def _build_store():  # noqa: ANN202 - returns a bucket store
    """Pick the backing store once per app: Redis if reachable, else in-memory."""
    settings = get_settings()
    url = settings.redis_url
    if url:
        try:
            import redis  # guarded: optional dependency / may be unreachable

            client = redis.Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25)
            client.ping()
            _log.info("rate_limit_store", backend="redis")
            return RedisBucketStore(client)
        except Exception:  # noqa: BLE001 - any failure -> in-memory fallback
            _log.info("rate_limit_store", backend="memory", reason="redis_unreachable")
    return InMemoryBucketStore()


def _store(request: Request):  # noqa: ANN202
    store = getattr(request.app.state, "rate_limit_store", None)
    if store is None:
        store = _build_store()
        request.app.state.rate_limit_store = store
    return store


def _auth_limit(request: Request) -> int:
    return int(getattr(request.app.state, "rate_limit_auth_per_min", 0) or 0)


def _ai_limit(request: Request) -> int:
    return int(getattr(request.app.state, "rate_limit_ai_per_min", 0) or 0)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce(request: Request, *, key: str, limit: int, kind: str) -> None:
    if limit <= 0:
        return
    allowed, retry_after = _store(request).check(key, limit)
    if allowed:
        return
    _log.warning(
        "rate_limited",
        kind=kind,
        key=key,
        limit_per_min=limit,
        retry_after=retry_after,
        path=request.url.path,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please slow down.",
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit_ip() -> Callable[..., None]:
    """Dependency: per-IP limiter for the auth endpoints."""

    def _dep(request: Request) -> None:
        _enforce(
            request,
            key=f"auth:{_client_ip(request)}",
            limit=_auth_limit(request),
            kind="auth_ip",
        )

    return _dep


def rate_limit_user() -> Callable[..., None]:
    """Dependency: per-user limiter for the AI run endpoints."""

    def _dep(request: Request, user: Annotated[User, Depends(current_user)]) -> None:
        _enforce(
            request,
            key=f"ai:{user.id}",
            limit=_ai_limit(request),
            kind="ai_user",
        )

    return _dep
