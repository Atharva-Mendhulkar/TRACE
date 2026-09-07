"""
Microsoft Semantic Kernel Framework Adapter (PRD §11.2, §11.6, §36).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List
from uuid import uuid4

from trace.adapters.base import FrameworkAdapter, RawTraceEvent, register_adapter
from trace.schema.models import (
    CESRecord,
    EventAttributes,
    ErrorInfo,
    ProvenanceInfo,
    ValidationResult,
    compute_param_schema_hash,
)


class SemanticKernelAdapter(FrameworkAdapter):
    """Adapter for Microsoft Semantic Kernel plugin, function, and memory events."""

    framework = "semantic_kernel"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["1.0", "v1", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("semantic_kernel_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "plugin_name" in raw_event or "function_name" in raw_event or "kernel_id" in raw_event:
            return "1.0"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "function_name" not in raw_event
            and "plugin_name" not in raw_event
            and "event" not in raw_event
            and "type" not in raw_event
            and "name" not in raw_event
        ):
            errors.append("Missing Semantic Kernel plugin, function, or event identifier")
        if "trace_id" not in raw_event and "kernel_id" not in raw_event and "invocation_id" not in raw_event:
            errors.append("Missing trace correlation identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(
            raw_event.get("trace_id")
            or raw_event.get("kernel_id")
            or raw_event.get("invocation_id")
            or uuid4()
        )
        span_id = str(raw_event.get("span_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or "sk-agent")
        role = raw_event.get("role")
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no") or raw_event.get("step", 0))
        schema_version = self.detect_schema_version(raw_event)

        event_kind = raw_event.get("event") or raw_event.get("type", "")
        plugin = raw_event.get("plugin_name") or raw_event.get("plugin")
        function = raw_event.get("function_name") or raw_event.get("function") or raw_event.get("name")
        symbol_name = f"{plugin}_{function}" if plugin and function else (function or plugin or "kernel_step")

        # 1. Memory read / search
        if (
            event_kind in ("memory_read", "semantic_memory_search", "memory_search")
            or (plugin and "memory" in plugin.lower() and "search" in str(function).lower())
        ):
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="memory_read",
                    raw_symbol=symbol_name,
                    canonical_symbol_candidate=symbol_name,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 2. Memory write / save
        elif (
            event_kind in ("memory_write", "semantic_memory_save", "memory_save")
            or (plugin and "memory" in plugin.lower() and "save" in str(function).lower())
        ):
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="memory_write",
                    raw_symbol=symbol_name,
                    canonical_symbol_candidate=symbol_name,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 3. Delegation
        elif event_kind in ("delegate", "subagent_invoke", "agent_transfer") or raw_event.get("target_agent"):
            target = raw_event.get("target_agent") or raw_event.get("target_role") or "subagent"
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=str(target),
                    depth=depth,
                    event_type="delegate",
                    raw_symbol=f"delegate({target})",
                    canonical_symbol_candidate=f"delegate({target})",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 4. Function Invoking (Tool Call)
        elif event_kind in ("function_invoking", "tool_call", "function_call") or (function and "result" not in raw_event and event_kind != "function_invoked"):
            args = raw_event.get("arguments") or raw_event.get("variables") or raw_event.get("parameters") or {}
            param_shape = {k: type(v).__name__ for k, v in args.items()} if isinstance(args, dict) else {}

            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="tool_call",
                    raw_symbol=symbol_name,
                    canonical_symbol_candidate=symbol_name,
                    param_schema=param_shape,
                    status="unknown",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

            # Check if output is combined in the same event
            if "result" in raw_event or "output" in raw_event:
                events.append(
                    RawTraceEvent(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        agent_id=agent_id,
                        role=role,
                        depth=depth,
                        event_type="tool_result",
                        raw_symbol=f"{symbol_name}_result",
                        canonical_symbol_candidate=f"{symbol_name}_result",
                        param_schema={},
                        status="success",
                        timestamp=timestamp,
                        framework="semantic_kernel",
                        framework_schema_version=schema_version,
                        sequence_no=seq_no + 1,
                    )
                )

        # 5. Function Invoked (Tool Result standalone)
        elif event_kind in ("function_invoked", "tool_result") or "result" in raw_event:
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="tool_result",
                    raw_symbol=f"{symbol_name}_result",
                    canonical_symbol_candidate=f"{symbol_name}_result",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 6. Termination
        elif event_kind in ("pipeline_end", "plan_executed", "terminate"):
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="terminate",
                    raw_symbol="terminate",
                    canonical_symbol_candidate="terminate",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 7. Error
        elif event_kind in ("error", "filter_exception") or "error" in raw_event:
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol="function_error",
                    param_schema={},
                    status="failure",
                    error_class="kernel_exception",
                    retryable_error=False,
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 8. Fallback: plan_step
        else:
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="plan_step",
                    raw_symbol=symbol_name,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="semantic_kernel",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        return events

    def to_ces(self, raw_trace_event: RawTraceEvent) -> CESRecord:
        param_hash = compute_param_schema_hash(raw_trace_event.param_schema)
        symbol = raw_trace_event.canonical_symbol_candidate or raw_trace_event.raw_symbol

        error_obj = None
        if raw_trace_event.error_class:
            error_obj = ErrorInfo(
                error_class=raw_trace_event.error_class,
                retryable=raw_trace_event.retryable_error,
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
            event_type=raw_trace_event.event_type,  # type: ignore
            symbol=symbol,
            raw_symbol=raw_trace_event.raw_symbol,
            attributes=EventAttributes(
                param_schema_hash=param_hash,
                status=raw_trace_event.status if raw_trace_event.status in ["success", "failure", "timeout"] else "unknown",  # type: ignore
                latency_ms=raw_trace_event.latency_ms,
                retry_count=raw_trace_event.retry_count,
            ),
            error=error_obj,
            timestamp=raw_trace_event.timestamp,
            framework="semantic_kernel",
            framework_schema_version=raw_trace_event.framework_schema_version,
            adapter_version=self.adapter_version,
            sequence_no=raw_trace_event.sequence_no,
            status=raw_trace_event.status if raw_trace_event.status in ["success", "failure", "timeout"] else "unknown",  # type: ignore
            provenance=ProvenanceInfo(
                timestamp_source="framework",
                ingested_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )


# Register singleton
register_adapter(SemanticKernelAdapter())
