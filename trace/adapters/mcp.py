"""
Model Context Protocol (MCP) Adapter (PRD §11.6).
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


class MCPAdapter(FrameworkAdapter):
    """Adapter for Model Context Protocol (MCP) JSON-RPC 2.0 tool executions."""

    framework = "mcp"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["2024-11-05", "draft", "1.0"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("mcp_version") or raw_event.get("protocolVersion")
        if version and version in self.supported_framework_schema_versions:
            return str(version)
        if raw_event.get("jsonrpc") == "2.0":
            return "2024-11-05"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if raw_event.get("jsonrpc") != "2.0" and "method" not in raw_event and "result" not in raw_event:
            errors.append("Missing jsonrpc 2.0 or method/result structure")
        if "trace_id" not in raw_event and "conversation_id" not in raw_event and "id" not in raw_event:
            errors.append("Missing trace correlation identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(raw_event.get("trace_id") or raw_event.get("conversation_id") or uuid4())
        span_id = str(raw_event.get("span_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or "mcp-agent")
        role = raw_event.get("role")
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no", 0))
        schema_version = self.detect_schema_version(raw_event)

        method = raw_event.get("method", "")
        params = raw_event.get("params", {})

        # Handle tool call
        if method == "tools/call" or "name" in params or raw_event.get("type") == "tool_call":
            tool_name = params.get("name") or raw_event.get("name") or "unknown_tool"
            arguments = params.get("arguments") or params.get("parameters") or {}
            
            # Param schema shape (keys + types, not values)
            param_shape = {k: type(v).__name__ for k, v in arguments.items()} if isinstance(arguments, dict) else {}

            call_event = RawTraceEvent(
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
                framework="mcp",
                framework_schema_version=schema_version,
                sequence_no=seq_no,
                metadata={"rpc_id": raw_event.get("id")},
            )
            events.append(call_event)

            # If combined with result
            if "result" in raw_event or "error" in raw_event:
                res_seq = seq_no + 1
                is_error = "error" in raw_event
                status = "failure" if is_error else "success"
                err_class = "tool_error" if is_error else None
                result_event = RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error" if is_error else "tool_result",
                    raw_symbol=f"{tool_name}_result",
                    canonical_symbol_candidate=f"{tool_name}_result",
                    param_schema={},
                    status=status,
                    error_class=err_class,
                    retryable_error=False,
                    timestamp=timestamp,
                    framework="mcp",
                    framework_schema_version=schema_version,
                    sequence_no=res_seq,
                    metadata={"rpc_id": raw_event.get("id")},
                )
                events.append(result_event)

        # Handle tool result standalone
        elif "result" in raw_event:
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="tool_result",
                    raw_symbol=str(raw_event.get("tool_name", "tool")) + "_result",
                    canonical_symbol_candidate=str(raw_event.get("tool_name", "tool")) + "_result",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="mcp",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"rpc_id": raw_event.get("id")},
                )
            )

        # Handle errors
        elif "error" in raw_event:
            err_obj = raw_event["error"]
            err_code = err_obj.get("code") if isinstance(err_obj, dict) else -32000
            err_class = "timeout" if err_code == -32001 else "tool_error"
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol="rpc_error",
                    param_schema={},
                    status="failure",
                    error_class=err_class,
                    retryable_error=True if err_code == -32001 else False,
                    timestamp=timestamp,
                    framework="mcp",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # Fallback event
        else:
            event_type = raw_event.get("event_type", "plan_step")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type=event_type,
                    raw_symbol=str(raw_event.get("symbol") or method or event_type),
                    param_schema={},
                    status=raw_event.get("status", "success"),
                    timestamp=timestamp,
                    framework="mcp",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        return events

    def to_ces(self, raw_trace_event: RawTraceEvent) -> CESRecord:
        param_hash = compute_param_schema_hash(raw_trace_event.param_schema)
        # Phase 1: symbol is raw_symbol or canonical candidate
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
            framework="mcp",
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
register_adapter(MCPAdapter())
