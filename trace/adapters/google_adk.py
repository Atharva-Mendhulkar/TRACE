"""
Google Agent Development Kit (ADK) Framework Adapter (PRD §11.2, §11.6, §36).
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


class GoogleADKAdapter(FrameworkAdapter):
    """Adapter for Google ADK / Vertex AI Agent events."""

    framework = "google_adk"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["0.1", "1.0", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("adk_version") or raw_event.get("google_adk_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "invocation_id" in raw_event or "session_id" in raw_event or "turn_id" in raw_event:
            return "1.0"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "function_call" not in raw_event
            and "tool_call" not in raw_event
            and "event" not in raw_event
            and "type" not in raw_event
            and "action" not in raw_event
            and "name" not in raw_event
        ):
            errors.append("Missing Google ADK action, function call, or event structure")
        if "trace_id" not in raw_event and "session_id" not in raw_event and "invocation_id" not in raw_event:
            errors.append("Missing trace correlation identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(
            raw_event.get("trace_id")
            or raw_event.get("session_id")
            or raw_event.get("invocation_id")
            or uuid4()
        )
        span_id = str(raw_event.get("span_id") or raw_event.get("turn_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or "google-adk-agent")
        role = raw_event.get("role")
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no") or raw_event.get("step", 0))
        schema_version = self.detect_schema_version(raw_event)

        event_kind = raw_event.get("event") or raw_event.get("type", "")

        # 1. Delegation / Sub-agent dispatch
        if event_kind in ("dispatch", "delegate_agent", "delegate", "call_agent") or raw_event.get("dispatch_to"):
            target = raw_event.get("dispatch_to") or raw_event.get("target_agent") or raw_event.get("role") or "subagent"
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
                    framework="google_adk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 2. Tool / Function Call
        elif "function_call" in raw_event or event_kind in ("tool_call", "function_call"):
            fn = raw_event.get("function_call") if isinstance(raw_event.get("function_call"), dict) else raw_event
            tool_name = fn.get("name") or raw_event.get("name", "tool")
            args = fn.get("args") or fn.get("arguments") or raw_event.get("args", {})
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
                    raw_symbol=tool_name,
                    canonical_symbol_candidate=tool_name,
                    param_schema=param_shape,
                    status="unknown",
                    timestamp=timestamp,
                    framework="google_adk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

            # If combined with response
            if "response" in raw_event or "result" in raw_event:
                events.append(
                    RawTraceEvent(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        agent_id=agent_id,
                        role=role,
                        depth=depth,
                        event_type="tool_result",
                        raw_symbol=f"{tool_name}_result",
                        canonical_symbol_candidate=f"{tool_name}_result",
                        param_schema={},
                        status="success",
                        timestamp=timestamp,
                        framework="google_adk",
                        framework_schema_version=schema_version,
                        sequence_no=seq_no + 1,
                    )
                )

        # 3. Tool / Function Response standalone
        elif "function_response" in raw_event or event_kind in ("function_response", "tool_result", "tool_output"):
            fn_resp = raw_event.get("function_response") if isinstance(raw_event.get("function_response"), dict) else raw_event
            t_name = fn_resp.get("name") or raw_event.get("name", "tool")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="tool_result",
                    raw_symbol=f"{t_name}_result",
                    canonical_symbol_candidate=f"{t_name}_result",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="google_adk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 4. Termination
        elif event_kind in ("turn_complete", "session_complete", "end_turn", "terminate"):
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
                    framework="google_adk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 5. Error
        elif event_kind in ("adk_error", "error") or "error" in raw_event:
            err_msg = str(raw_event.get("error") or "google_adk_error")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol="adk_error",
                    param_schema={},
                    status="failure",
                    error_class="adk_execution_failure",
                    retryable_error=False,
                    timestamp=timestamp,
                    framework="google_adk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"error": err_msg},
                )
            )

        # 6. Fallback: plan step
        else:
            step_name = str(raw_event.get("name") or event_kind or "model_turn")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="plan_step",
                    raw_symbol=step_name,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="google_adk",
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
            framework="google_adk",
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
register_adapter(GoogleADKAdapter())
