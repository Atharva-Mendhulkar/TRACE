"""
Streaming Ingestion Consumers & Real-Time Verification Workers (PRD §11.1, §35).
Supports async queue pipelines and Redis Streams with consumer groups and DLQ routing.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import json
import logging
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from trace.ingestion.pipeline import IngestionPipeline
from trace.schema.models import CESRecord, validate_ces_record
from trace.verification.session_cache import SessionCache
from trace.verification.verifier import RuntimeVerifier, VerificationResponse

logger = logging.getLogger("trace.streaming")


class StreamConsumer(ABC):
    """Abstract interface for event stream consumers."""

    @abstractmethod
    async def start(self, handler: Callable[[CESRecord], Any]) -> None:
        """Begin consuming events from the stream and invoke handler on each valid record."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down stream consumption."""
        pass


class AsyncQueueConsumer(StreamConsumer):
    """Asynchronous in-memory stream consumer for microsecond-latency processing and testing."""

    def __init__(self, queue: Optional[asyncio.Queue] = None):
        self.queue: asyncio.Queue[Dict[str, Any]] = queue or asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.dead_letter_queue: List[Dict[str, Any]] = []

    async def put_event(self, raw_event: Dict[str, Any]) -> None:
        """Push an incoming event to the queue."""
        await self.queue.put(raw_event)

    async def start(self, handler: Callable[[CESRecord], Any]) -> None:
        self._running = True
        self._task = asyncio.create_task(self._consume_loop(handler))

    async def _consume_loop(self, handler: Callable[[CESRecord], Any]) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                val = validate_ces_record(item)
                if not val.is_valid:
                    self.dead_letter_queue.append({"event": item, "errors": val.errors})
                    self.queue.task_done()
                    continue

                record = CESRecord.model_validate(item)
                res = handler(record)
                if asyncio.iscoroutine(res):
                    await res
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error processing queue stream item: %s", e)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class RedisStreamConsumer(StreamConsumer):
    """
    Redis Streams consumer using consumer groups, message acknowledgements,
    and dead-letter queue routing for production stream ingestion (PRD §35).
    """

    def __init__(
        self,
        stream_key: str = "trace:stream:events",
        group_name: str = "trace-verifiers",
        consumer_name: Optional[str] = None,
        redis_url: str = "redis://localhost:6379/0",
        dlq_key: str = "trace:stream:dlq",
    ):
        self.stream_key = stream_key
        self.group_name = group_name
        self.consumer_name = consumer_name or f"consumer-{uuid4().hex[:8]}"
        self.redis_url = redis_url
        self.dlq_key = dlq_key
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._redis = None

    async def start(self, handler: Callable[[CESRecord], Any]) -> None:
        self._running = True
        try:
            import redis
            self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
            # Create consumer group if not exists
            try:
                self._redis.xgroup_create(self.stream_key, self.group_name, id="0", mkstream=True)
            except Exception:
                pass  # group may already exist
            self._task = asyncio.create_task(self._consume_loop(handler))
        except Exception as e:
            logger.warning("Redis stream connection unavailable (%s); worker disabled", e)

    async def _consume_loop(self, handler: Callable[[CESRecord], Any]) -> None:
        while self._running and self._redis:
            try:
                entries = self._redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=10,
                    block=200,
                )
                if not entries:
                    await asyncio.sleep(0.05)
                    continue

                for stream, messages in entries:
                    for msg_id, data in messages:
                        try:
                            payload_str = data.get("payload") or json.dumps(data)
                            event_dict = json.loads(payload_str) if isinstance(payload_str, str) else data
                            val = validate_ces_record(event_dict)
                            if not val.is_valid:
                                # Route to DLQ
                                self._redis.xadd(self.dlq_key, {"payload": json.dumps(event_dict), "errors": json.dumps(val.errors)})
                                self._redis.xack(self.stream_key, self.group_name, msg_id)
                                continue

                            record = CESRecord.model_validate(event_dict)
                            res = handler(record)
                            if asyncio.iscoroutine(res):
                                await res

                            # Acknowledge
                            self._redis.xack(self.stream_key, self.group_name, msg_id)
                        except Exception as inner_e:
                            logger.error("Error processing Redis stream message %s: %s", msg_id, inner_e)
                            self._redis.xadd(self.dlq_key, {"payload": str(data), "error": str(inner_e)})
                            self._redis.xack(self.stream_key, self.group_name, msg_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Redis stream read loop exception: %s", e)
                await asyncio.sleep(0.5)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class StreamingVerificationWorker:
    """
    Worker coordinating a StreamConsumer, RuntimeVerifier, and SessionCache,
    publishing violation alerts and updating live state.
    """

    def __init__(
        self,
        consumer: StreamConsumer,
        verifier: RuntimeVerifier,
        session_cache: Optional[SessionCache] = None,
        violation_callback: Optional[Callable[[VerificationResponse], Any]] = None,
    ):
        self.consumer = consumer
        self.verifier = verifier
        self.session_cache = session_cache
        self.violation_callback = violation_callback
        self.processed_count: int = 0
        self.violations_detected: int = 0

    async def start(self) -> None:
        await self.consumer.start(self._on_event)

    async def _on_event(self, event: CESRecord) -> None:
        resp = self.verifier.verify_event(event, session_cache=self.session_cache)
        self.processed_count += 1
        if resp.violation or resp.classification:
            self.violations_detected += 1
            if self.violation_callback:
                cb = self.violation_callback(resp)
                if asyncio.iscoroutine(cb):
                    await cb

    async def stop(self) -> None:
        await self.consumer.stop()
