"""
CrewAI Framework Adapter (PRD §11.2, §11.6, §36).
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


class CrewAIAdapter(FrameworkAdapter):
    """Adapter for CrewAI multi-agent task and tool execution events."""

    framework = "crewai"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["0.1", "0.2", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("crewai_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "task" in raw_event or "crew_id" in raw_event or "agent_role" in raw_event:
            return "0.2"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "task" not in raw_event
            and "event" not in raw_event
            and "type" not in raw_event
            and "tool" not in raw_event
            and "action" not in raw_event
        ):
            errors.append("Missing CrewAI task, event, or action identifier")
        if "trace_id" not in raw_event and "crew_id" not in raw_event and "run_id" not in raw_event:
            errors.append("Missing trace correlation identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(
            raw_event.get("trace_id")
            or raw_event.get("crew_id")
            or raw_event.get("run_id")
            or uuid4()
        )
        span_id = str(raw_event.get("span_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or raw_event.get("agent") or "crewai-agent")
        role = raw_event.get("agent_role") or raw_event.get("role")
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no") or raw_event.get("step", 0))
        schema_version = self.detect_schema_version(raw_event)

        event_kind = raw_event.get("event") or raw_event.get("type") or raw_event.get("action", "")
        tool_name = (
            raw_event.get("tool_name")
            or raw_event.get("tool")
            or raw_event.get("name")
        )

        # 1. Delegation to coworker
        if (
            event_kind in ("delegate_work_to_coworker", "ask_question_to_coworker", "delegate")
            or tool_name in ("delegate_work_to_coworker", "ask_question_to_coworker")
        ):
            params = raw_event.get("params") or raw_event.get("tool_input") or {}
            coworker = (
                raw_event.get("coworker")
                or raw_event.get("target_role")
                or (params.get("coworker") if isinstance(params, dict) else None)
                or "coworker"
            )
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=str(coworker),
                    depth=depth,
                    event_type="delegate",
                    raw_symbol=f"delegate({coworker})",
                    canonical_symbol_candidate=f"delegate({coworker})",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="crewai",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"target_role": coworker},
                )
            )

        # 2. Tool Execution
        elif tool_name or event_kind in ("tool_usage", "tool_call", "use_tool"):
            actual_tool = tool_name or "tool"
            raw_args = raw_event.get("tool_input") or raw_event.get("arguments") or raw_event.get("params") or {}
            param_shape = {k: type(v).__name__ for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}

            call_event = RawTraceEvent(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                agent_id=agent_id,
                role=role,
                depth=depth,
                event_type="tool_call",
                raw_symbol=str(actual_tool),
                canonical_symbol_candidate=str(actual_tool),
                param_schema=param_shape,
                status="unknown",
                timestamp=timestamp,
                framework="crewai",
                framework_schema_version=schema_version,
                sequence_no=seq_no,
            )
            events.append(call_event)

            if "result" in raw_event or "output" in raw_event:
                res_seq = seq_no + 1
                is_error = raw_event.get("status") == "failure" or "error" in raw_event
                events.append(
                    RawTraceEvent(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        agent_id=agent_id,
                        role=role,
                        depth=depth,
                        event_type="error" if is_error else "tool_result",
                        raw_symbol=f"{actual_tool}_result",
                        canonical_symbol_candidate=f"{actual_tool}_result",
                        param_schema={},
                        status="failure" if is_error else "success",
                        timestamp=timestamp,
                        framework="crewai",
                        framework_schema_version=schema_version,
                        sequence_no=res_seq,
                    )
                )

        # 3. Tool Result Standalone
        elif event_kind in ("tool_output", "tool_result"):
            t_name = str(raw_event.get("tool", "tool"))
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
                    framework="crewai",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 4. Termination
        elif event_kind in ("crew_finish", "task_finish", "agent_finish", "terminate"):
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
                    framework="crewai",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                )
            )

        # 5. Error
        elif event_kind in ("error", "task_error") or "error" in raw_event:
            err_msg = str(raw_event.get("error") or "crewai_error")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol="task_error",
                    param_schema={},
                    status="failure",
                    error_class="crew_execution_error",
                    retryable_error=False,
                    timestamp=timestamp,
                    framework="crewai",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no,
                    metadata={"error_message": err_msg},
                )
            )

        # 6. Default: plan step
        else:
            task_desc = str(raw_event.get("task") or event_kind or "step")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="plan_step",
                    raw_symbol=task_desc,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="crewai",
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
            framework="crewai",
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
register_adapter(CrewAIAdapter())
