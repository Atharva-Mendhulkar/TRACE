"""
Real-Time Stream Session State Caching (PRD §17, §29).
Supports Redis caching with automatic in-memory fallback for hot-path verification.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import datetime
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger("trace.verification.session_cache")


@dataclass
class VerificationSessionState:
    """Live state for an active trace/span stream during runtime verification."""

    trace_id: str
    span_id: str
    model_id: Optional[str]
    policy_id: Optional[str]
    current_learned_state: str
    current_policy_state: str
    running_mean_nll: float = 0.0
    running_events_count: int = 0
    last_event_time: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> VerificationSessionState:
        parsed = json.loads(data)
        return cls(**parsed)


class SessionCache(ABC):
    """Abstract interface for active verification session state caching."""

    @abstractmethod
    def get_session(self, trace_id: str, span_id: str) -> Optional[VerificationSessionState]:
        """Retrieve live verification state for a given (trace_id, span_id)."""
        pass

    @abstractmethod
    def set_session(
        self, session: VerificationSessionState, ttl_seconds: int = 3600
    ) -> None:
        """Store or update live verification state with TTL."""
        pass

    @abstractmethod
    def delete_session(self, trace_id: str, span_id: str) -> None:
        """Evict session upon trace completion or timeout."""
        pass


class InMemorySessionCache(SessionCache):
    """Thread-safe in-memory session cache for development, testing, and fallback."""

    def __init__(self):
        self._store: Dict[str, str] = {}

    def _key(self, trace_id: str, span_id: str) -> str:
        return f"trace:session:{trace_id}:{span_id}"

    def get_session(self, trace_id: str, span_id: str) -> Optional[VerificationSessionState]:
        key = self._key(trace_id, span_id)
        raw = self._store.get(key)
        if not raw:
            return None
        return VerificationSessionState.from_json(raw)

    def set_session(
        self, session: VerificationSessionState, ttl_seconds: int = 3600
    ) -> None:
        key = self._key(session.trace_id, session.span_id)
        self._store[key] = session.to_json()

    def delete_session(self, trace_id: str, span_id: str) -> None:
        key = self._key(trace_id, span_id)
        self._store.pop(key, None)


class RedisSessionCache(SessionCache):
    """
    High-throughput Redis session cache implementing PRD §17, §29.
    Falls back gracefully to InMemorySessionCache if Redis server is unreachable.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._fallback = InMemorySessionCache()
        self._redis_client = None
        self._use_fallback = False

        try:
            import redis
            client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=0.5)
            client.ping()
            self._redis_client = client
        except Exception as e:
            logger.info("Redis server not available at %s (%s); utilizing in-memory session cache", redis_url, e)
            self._use_fallback = True

    def _key(self, trace_id: str, span_id: str) -> str:
        return f"trace:session:{trace_id}:{span_id}"

    def get_session(self, trace_id: str, span_id: str) -> Optional[VerificationSessionState]:
        if self._use_fallback or not self._redis_client:
            return self._fallback.get_session(trace_id, span_id)

        try:
            raw = self._redis_client.get(self._key(trace_id, span_id))
            if not raw:
                return None
            return VerificationSessionState.from_json(raw)
        except Exception as e:
            logger.warning("Redis get_session failed (%s); failing over to local cache", e)
            return self._fallback.get_session(trace_id, span_id)

    def set_session(
        self, session: VerificationSessionState, ttl_seconds: int = 3600
    ) -> None:
        if self._use_fallback or not self._redis_client:
            self._fallback.set_session(session, ttl_seconds)
            return

        try:
            key = self._key(session.trace_id, session.span_id)
            self._redis_client.setex(key, ttl_seconds, session.to_json())
        except Exception as e:
            logger.warning("Redis set_session failed (%s); failing over to local cache", e)
            self._fallback.set_session(session, ttl_seconds)

    def delete_session(self, trace_id: str, span_id: str) -> None:
        if self._use_fallback or not self._redis_client:
            self._fallback.delete_session(trace_id, span_id)
            return

        try:
            self._redis_client.delete(self._key(trace_id, span_id))
        except Exception as e:
            logger.warning("Redis delete_session failed (%s); failing over to local cache", e)
            self._fallback.delete_session(trace_id, span_id)
