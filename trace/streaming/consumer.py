"""
Streaming Ingestion Consumers & Real-Time Verification Workers (PRD §11.1, §35).
Async queue pipeline with dead-letter routing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from trace.schema.models import CESRecord
from trace.verification.verifier import RuntimeVerifier, VerificationResponse

from pydantic import ValidationError

logger = logging.getLogger("trace.streaming")


class AsyncQueueConsumer:
    """Asynchronous in-memory stream consumer with dead-letter queue."""

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
                try:
                    record = CESRecord.model_validate(item)
                except ValidationError as e:
                    self.dead_letter_queue.append({
                        "event": item,
                        "errors": [f"{err['loc']}: {err['msg']}" for err in e.errors()],
                    })
                    self.queue.task_done()
                    continue

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


class StreamingVerificationWorker:
    """Worker coordinating an AsyncQueueConsumer and RuntimeVerifier."""

    def __init__(
        self,
        consumer: AsyncQueueConsumer,
        verifier: RuntimeVerifier,
        violation_callback: Optional[Callable[[VerificationResponse], Any]] = None,
    ):
        self.consumer = consumer
        self.verifier = verifier
        self.violation_callback = violation_callback
        self.processed_count: int = 0
        self.violations_detected: int = 0

    async def start(self) -> None:
        await self.consumer.start(self._on_event)

    async def _on_event(self, event: CESRecord) -> None:
        resp = self.verifier.verify_event(event)
        self.processed_count += 1
        if resp.violation or resp.classification:
            self.violations_detected += 1
            if self.violation_callback:
                cb = self.violation_callback(resp)
                if asyncio.iscoroutine(cb):
                    await cb

    async def stop(self) -> None:
        await self.consumer.stop()
