"""
SWE-bench Execution Trajectory Framework Adapter (Phase 4 / Benchmark Ingestion).
Normalizes SWE-agent and benchmark trajectories into Canonical Event Schema (CES v1.0).
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


class SWEBenchAdapter(FrameworkAdapter):
    """Adapter for SWE-bench (SWE-agent / AutoCodeRover / benchmark trajectories)."""

    framework = "swebench"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["1.0", "swe-agent-v1", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("schema_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "instance_id" in raw_event or "history" in raw_event or "trajectory" in raw_event:
            return "swe-agent-v1"
        return "1.0"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "instance_id" not in raw_event
            and "trace_id" not in raw_event
            and "action" not in raw_event
            and "history" not in raw_event
            and "trajectory" not in raw_event
        ):
            errors.append("Missing SWE-bench instance_id, trace_id, action, or history trajectory")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        instance_id = str(raw_event.get("instance_id") or raw_event.get("trace_id") or uuid4())
        agent_id = str(raw_event.get("agent_id") or "swe-agent")
        base_timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        schema_version = self.detect_schema_version(raw_event)

        # Case A: Full Trajectory with 'history' or 'trajectory' list
        history = raw_event.get("history") or raw_event.get("trajectory")
        if isinstance(history, list) and len(history) > 0:
            for idx, item in enumerate(history):
                step_event = self._parse_single_step(
                    step_data=item,
                    trace_id=instance_id,
                    agent_id=agent_id,
                    seq_no=idx,
                    schema_version=schema_version,
                    base_timestamp=base_timestamp,
                )
                if step_event:
                    events.append(step_event)
            return events

        # Case B: Single Action Event
        single = self._parse_single_step(
            step_data=raw_event,
            trace_id=instance_id,
            agent_id=agent_id,
            seq_no=int(raw_event.get("sequence_no") or raw_event.get("step", 0)),
            schema_version=schema_version,
            base_timestamp=base_timestamp,
        )
        if single:
            events.append(single)
        return events

    def _parse_single_step(
        self,
        step_data: Dict[str, Any],
        trace_id: str,
        agent_id: str,
        seq_no: int,
        schema_version: str,
        base_timestamp: str,
    ) -> Optional[RawTraceEvent]:
        action_name = (
            step_data.get("action")
            or step_data.get("command")
            or step_data.get("tool")
            or step_data.get("name")
            or "unknown_action"
        )
        action_str = str(action_name).strip()
        args = step_data.get("args") or step_data.get("parameters") or {}
        output = step_data.get("output") or step_data.get("observation") or ""
        status_raw = str(step_data.get("status", "success")).lower()
        status = "success" if status_raw in ("success", "ok", "0", 0) else "failure"

        # Categorize action into CES event_type
        if action_str in ("submit", "finish", "exit", "task_done", "complete"):
            event_type = "terminate"
            raw_symbol = "submit"
        elif any(verb in action_str for verb in ("test", "pytest", "runtest")):
            event_type = "tool_call"
            raw_symbol = "run_test"
        elif any(verb in action_str for verb in ("edit", "patch", "modify", "write", "replace")):
            event_type = "tool_call"
            raw_symbol = "edit_file"
        elif any(verb in action_str for verb in ("open", "read", "view", "cat", "show")):
            event_type = "tool_call"
            raw_symbol = "read_file"
        elif any(verb in action_str for verb in ("search", "find", "grep", "locate")):
            event_type = "tool_call"
            raw_symbol = "search_code"
        elif any(verb in action_str for verb in ("bash", "cmd", "exec", "run")):
            event_type = "tool_call"
            raw_symbol = "bash_command"
        elif "delegate" in action_str or "subagent" in action_str:
            event_type = "delegate"
            raw_symbol = f"delegate({args.get('role', 'subagent')})"
        elif "error" in status_raw or "traceback" in str(output).lower():
            event_type = "error"
            raw_symbol = "execution_error"
        else:
            event_type = "tool_call"
            raw_symbol = action_str

        return RawTraceEvent(
            trace_id=trace_id,
            span_id=str(step_data.get("span_id") or f"span-{trace_id}-{seq_no}"),
            parent_span_id=step_data.get("parent_span_id"),
            agent_id=agent_id,
            role="swe_engineer",
            depth=0,
            event_type=event_type,
            raw_symbol=raw_symbol,
            canonical_symbol_candidate=raw_symbol,
            param_schema=args if isinstance(args, dict) else {"arg_raw": str(args)},
            status=status,
            timestamp=step_data.get("timestamp") or base_timestamp,
            framework=self.framework,
            framework_schema_version=schema_version,
            sequence_no=seq_no,
            metadata={
                "action": action_str,
                "output_preview": str(output)[:200] if output else None,
            },
        )

    def to_ces(self, raw_trace_event: RawTraceEvent) -> CESRecord:
        param_hash = compute_param_schema_hash(raw_trace_event.param_schema)
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return CESRecord(
            event_id=str(uuid4()),
            trace_id=raw_trace_event.trace_id,
            span_id=raw_trace_event.span_id,
            parent_span_id=raw_trace_event.parent_span_id,
            agent_id=raw_trace_event.agent_id,
            role=raw_trace_event.role,
            depth=raw_trace_event.depth,
            event_type=raw_trace_event.event_type,  # type: ignore
            symbol=raw_trace_event.canonical_symbol_candidate or raw_trace_event.raw_symbol,
            raw_symbol=raw_trace_event.raw_symbol,
            attributes=EventAttributes(
                param_schema_hash=param_hash,
                status=raw_trace_event.status,  # type: ignore
                latency_ms=raw_trace_event.latency_ms,
                retry_count=raw_trace_event.retry_count,
            ),
            status=raw_trace_event.status,  # type: ignore
            timestamp=raw_trace_event.timestamp,
            framework=self.framework,
            framework_schema_version=raw_trace_event.framework_schema_version,
            adapter_version=self.adapter_version,
            sequence_no=raw_trace_event.sequence_no,
            provenance=ProvenanceInfo(
                timestamp_source="framework",
                ingested_at=now_utc,
            ),
        )


# Self-register
swebench_adapter = SWEBenchAdapter()
register_adapter(swebench_adapter)
