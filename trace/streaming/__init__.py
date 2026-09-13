"""
TRACE Streaming Ingestion & Real-Time Processing Package (PRD §11.1, §35).
"""

from trace.streaming.consumer import (
    AsyncQueueConsumer,
    StreamingVerificationWorker,
)

__all__ = [
    "AsyncQueueConsumer",
    "StreamingVerificationWorker",
]
