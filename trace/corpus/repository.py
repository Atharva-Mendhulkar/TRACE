"""
Trace Repository Abstraction (PRD §13, §29).
Domain logic depends strictly on this repository interface, not on SQLite directly.
"""

from __future__ import annotations

from typing import List, Optional, Protocol
from trace.schema.models import CESRecord


class TraceRepository(Protocol):
    """Abstract repository for storing and querying canonical events and traces."""

    def write_event(self, record: CESRecord) -> bool:
        """Idempotently insert an event. Returns True if inserted, False if duplicate."""
        ...

    def write_events(self, records: List[CESRecord]) -> int:
        """Insert a batch of records. Returns count of newly inserted records."""
        ...

    def get_trace(self, trace_id: str, span_id: Optional[str] = None) -> List[CESRecord]:
        """Reconstruct ordered event sequence per (trace_id, span_id) using sequence_no."""
        ...

    def is_trace_complete(self, trace_id: str) -> bool:
        """Check if a trace contains a terminate event at depth 0."""
        ...

    def get_all_trace_ids(self, agent_id: Optional[str] = None) -> List[str]:
        """Return all distinct trace IDs, optionally filtered by agent_id."""
        ...

    def get_corpus(
        self,
        agent_id: str,
        include_truncated: bool = False,
        taxonomy_version: int = 1,
    ) -> List[List[str]]:
        """Return complete symbolic traces over alphabet Sigma for training."""
        ...
