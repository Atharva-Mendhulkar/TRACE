"""
OpenAI Agents SDK Framework Adapter (PRD §11.2, §11.6, §36).
"""

from __future__ import annotations

import datetime
import json
import re
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


class OpenAIAgentsAdapter(FrameworkAdapter):
    """Adapter for OpenAI Agents SDK / Swarm run and tool execution events."""

    framework = "openai_agents_sdk"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["1.0", "v1", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("openai_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "run_id" in raw_event or "tool_calls" in raw_event or "step_details" in raw_event:
            return "1.0"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "tool_calls" not in raw_event
            and "function" not in raw_event
            and "event" not in raw_event
            and "type" not in raw_event
            and "role" not in raw_event
            and "action" not in raw_event
        ):
            errors.append("Missing OpenAI agent action, function call, or event structure")
        if "trace_id" not in raw_event and "run_id" not in raw_event and "thread_id" not in raw_event:
            errors.append("Missing trace/run identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(
            raw_event.get("trace_id")
            or raw_event.get("run_id")
            or raw_event.get("thread_id")
            or uuid4()
        )
        span_id = str(raw_event.get("span_id") or raw_event.get("step_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or raw_event.get("agent_name") or "openai-agent")
        role = raw_event.get("role") if raw_event.get("role") not in ("tool", "assistant", "user", "system") else None
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no") or raw_event.get("step", 0))
        schema_version = self.detect_schema_version(raw_event)

        event_kind = raw_event.get("event") or raw_event.get("type", "")
        handoff_target = raw_event.get("target_agent") or raw_event.get("handoff_to")

        # 1. Handoff / Transfer to another agent
        if (
            event_kind in ("handoff", "agent_transfer", "delegate")
            or handoff_target is not None
            or str(raw_event.get("name", "")).startswith("transfer_to_")
        ):
            target = handoff_target or re.sub(r"^transfer_to_", "", str(raw_event.get("name", ""))) or "subagent"
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
                    framework="openai_agents_sdk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"target_agent": str(target)},
                )
            )

        # 2. Tool Calls list (e.g. OpenAI Assistants / Chat Completions tool_calls)
        elif "tool_calls" in raw_event and isinstance(raw_event["tool_calls"], list):
            for i, tc in enumerate(raw_event["tool_calls"]):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                fn_name = fn.get("name") or tc.get("name", "tool")
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                param_shape = {k: type(v).__name__ for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}

                events.append(
                    RawTraceEvent(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        agent_id=agent_id,
                        role=role,
                        depth=depth,
                        event_type="tool_call",
                        raw_symbol=fn_name,
                        canonical_symbol_candidate=fn_name,
                        param_schema=param_shape,
                        status="unknown",
                        timestamp=timestamp,
                        framework="openai_agents_sdk",
                        framework_schema_version=schema_version,
                        sequence_no=seq_no + i,
                        metadata={"tool_call_id": tc.get("id") if isinstance(tc, dict) else None},
                    )
                )

        # 3. Single tool call
        elif event_kind in ("tool_call", "function_call") or "function" in raw_event:
            fn = raw_event.get("function") if isinstance(raw_event.get("function"), dict) else raw_event
            fn_name = fn.get("name") or raw_event.get("name", "tool")
            raw_args = fn.get("arguments", {}) or raw_event.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except Exception:
                    raw_args = {}
            param_shape = {k: type(v).__name__ for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}

            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="tool_call",
                    raw_symbol=fn_name,
                    canonical_symbol_candidate=fn_name,
                    param_schema=param_shape,
                    status="unknown",
                    timestamp=timestamp,
                    framework="openai_agents_sdk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 4. Tool Output / Result (e.g. role == "tool" or event == "tool_result")
        elif raw_event.get("role") == "tool" or event_kind in ("tool_result", "tool_message", "tool_output"):
            tool_name = str(raw_event.get("name") or raw_event.get("tool_name") or "tool")
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
                    framework="openai_agents_sdk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 5. Run completed / Terminate
        elif (
            event_kind in ("run.completed", "step.completed", "terminate")
            or raw_event.get("finish_reason") == "stop"
            or raw_event.get("status") == "completed"
        ):
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
                    framework="openai_agents_sdk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 6. Error
        elif event_kind in ("run.failed", "error") or "error" in raw_event or raw_event.get("status") == "failed":
            err_msg = str(raw_event.get("error") or raw_event.get("last_error") or "openai_error")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol="run_error",
                    param_schema={},
                    status="failure",
                    error_class="openai_run_failure",
                    retryable_error=False,
                    timestamp=timestamp,
                    framework="openai_agents_sdk",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"error": err_msg},
                )
            )

        # 7. Fallback: plan step
        else:
            step_name = str(raw_event.get("name") or event_kind or "assistant_turn")
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
                    framework="openai_agents_sdk",
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
            framework="openai_agents_sdk",
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
register_adapter(OpenAIAgentsAdapter())
