"""
Framework Adapter Base Definitions and Registry (PRD §11).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type
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
    metadata: Dict[str, Any] = Field(default_factory=dict)


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

    @abstractmethod
    def to_ces(self, raw_trace_event: RawTraceEvent) -> CESRecord:
        """Map RawTraceEvent to Canonical Event Schema record with rule-based event_type."""
        pass


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
