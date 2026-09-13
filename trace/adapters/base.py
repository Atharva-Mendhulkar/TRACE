"""
Framework Adapter Base Definitions and Registry (PRD §11).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from trace.schema.models import CESRecord, FrameworkType, ValidationResult


class RawTraceEvent(BaseModel):
    """Normalized intermediate event parsed from framework logs before CES emission."""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    agent_id: str
    role: Optional[str] = None
    depth: int = 0
    event_type: str
    raw_symbol: str
    canonical_symbol_candidate: Optional[str] = None
    param_schema: Dict[str, Any] = Field(default_factory=dict)
    status: str = "unknown"
    latency_ms: Optional[int] = None
    retry_count: Optional[int] = None
    error_class: Optional[str] = None
    retryable_error: bool = False
    timestamp: str
    framework: FrameworkType
    framework_schema_version: str
    sequence_no: int = 0


class FrameworkAdapter(ABC):
    """Abstract interface that every framework adapter must implement."""

    framework: FrameworkType
    adapter_version: str
    supported_framework_schema_versions: List[str]

    @abstractmethod
    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        """Detect the framework schema version of an incoming raw event."""
        pass

    @abstractmethod
    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        """Validate framework event structure against expected framework schema."""
        pass

    @abstractmethod
    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        """Parse one framework event into one or more RawTraceEvents."""
        pass

    def to_ces(self, raw_trace_event: RawTraceEvent) -> CESRecord:
        """Map RawTraceEvent to Canonical Event Schema record."""
        import datetime
        from uuid import uuid4

        from trace.schema.models import ErrorInfo, EventAttributes, ProvenanceInfo, compute_param_schema_hash

        status = (
            raw_trace_event.status
            if raw_trace_event.status in ("success", "failure", "timeout")
            else "unknown"
        )
        error = (
            ErrorInfo(
                error_class=raw_trace_event.error_class,
                retryable=raw_trace_event.retryable_error,
            )
            if raw_trace_event.error_class
            else None
        )
        return CESRecord(
            schema_version="1.0",
            event_id=str(uuid4()),
            trace_id=raw_trace_event.trace_id,
            span_id=raw_trace_event.span_id,
            parent_span_id=raw_trace_event.parent_span_id,
            agent_id=raw_trace_event.agent_id,
            role=raw_trace_event.role,
            depth=raw_trace_event.depth,
            event_type=raw_trace_event.event_type,  # type: ignore[arg-type]
            symbol=raw_trace_event.canonical_symbol_candidate or raw_trace_event.raw_symbol,
            raw_symbol=raw_trace_event.raw_symbol,
            attributes=EventAttributes(
                param_schema_hash=compute_param_schema_hash(raw_trace_event.param_schema),
                status=status,  # type: ignore[arg-type]
                latency_ms=raw_trace_event.latency_ms,
                retry_count=raw_trace_event.retry_count,
            ),
            error=error,
            timestamp=raw_trace_event.timestamp,
            framework=self.framework,
            framework_schema_version=raw_trace_event.framework_schema_version,
            adapter_version=self.adapter_version,
            sequence_no=raw_trace_event.sequence_no,
            status=status,  # type: ignore[arg-type]
            provenance=ProvenanceInfo(
                timestamp_source="framework",
                ingested_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )


_ADAPTER_REGISTRY: Dict[str, FrameworkAdapter] = {}


def register_adapter(adapter: FrameworkAdapter) -> None:
    """Register a framework adapter instance."""
    _ADAPTER_REGISTRY[adapter.framework] = adapter


def get_adapter(framework: str) -> Optional[FrameworkAdapter]:
    """Retrieve registered adapter for a given framework."""
    return _ADAPTER_REGISTRY.get(framework)


def list_adapters() -> List[Dict[str, Any]]:
    """Enumerate registered adapters and their schema version support (PRD §11.8)."""
    return [
        {
            "framework": a.framework,
            "adapter_version": a.adapter_version,
            "supported_framework_schema_versions": a.supported_framework_schema_versions,
        }
        for a in _ADAPTER_REGISTRY.values()
    ]
