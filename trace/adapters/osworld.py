"""
OSWorld Desktop/Multimodal Agent Trajectory Framework Adapter (Phase 4 / Benchmark Ingestion).
Normalizes OSWorld desktop OS tasks into Canonical Event Schema (CES v1.0).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
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


class OSWorldAdapter(FrameworkAdapter):
    """Adapter for OSWorld multimodal desktop agent interaction traces."""

    framework = "osworld"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["1.0", "osworld-v1", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("schema_version") or raw_event.get("version")
        if version and str(version) in self.supported_framework_schema_versions:
            return str(version)
        if "task_id" in raw_event or "steps" in raw_event or "action_type" in raw_event:
            return "osworld-v1"
        return "1.0"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if (
            "task_id" not in raw_event
            and "trace_id" not in raw_event
            and "steps" not in raw_event
            and "action" not in raw_event
            and "action_type" not in raw_event
        ):
            errors.append("Missing OSWorld task_id, trace_id, steps, or action definition")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        task_id = str(raw_event.get("task_id") or raw_event.get("trace_id") or uuid4())
        agent_id = str(raw_event.get("agent_id") or "osworld-agent")
        base_timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        schema_version = self.detect_schema_version(raw_event)

        # Case A: Full Trajectory with 'steps' or 'actions' list
        steps = raw_event.get("steps") or raw_event.get("actions")
        if isinstance(steps, list) and len(steps) > 0:
            for idx, item in enumerate(steps):
                step_event = self._parse_single_step(
                    step_data=item,
                    trace_id=task_id,
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
            trace_id=task_id,
            agent_id=agent_id,
            seq_no=int(raw_event.get("sequence_no") or raw_event.get("step_idx", 0)),
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
        action_type = str(step_data.get("action_type") or step_data.get("type") or "").strip().lower()
        action_name = str(step_data.get("action") or step_data.get("name") or action_type or "unknown_os_action").strip()
        params = step_data.get("parameters") or step_data.get("args") or {}
        status_raw = str(step_data.get("status", "success")).lower()
        status = "success" if status_raw in ("success", "ok", "0", 0) else "failure"

        # Categorize desktop actions
        if action_type in ("terminate", "finish", "done", "complete") or action_name in ("task_complete", "finish_task"):
            event_type = "terminate"
            raw_symbol = "task_complete"
        elif any(act in action_name or act in action_type for act in ("click", "mouse", "drag", "press_mouse")):
            event_type = "tool_call"
            raw_symbol = "mouse_click"
        elif any(act in action_name or act in action_type for act in ("type", "key", "press_key", "shortcut")):
            event_type = "tool_call"
            raw_symbol = "keyboard_type"
        elif any(act in action_name or act in action_type for act in ("screenshot", "screen", "capture", "ocr")):
            event_type = "tool_call"
            raw_symbol = "screen_capture"
        elif any(act in action_name or act in action_type for act in ("launch", "app", "open_app", "open_url")):
            event_type = "tool_call"
            raw_symbol = "launch_application"
        elif any(act in action_name or act in action_type for act in ("terminal", "bash", "exec", "cmd")):
            event_type = "tool_call"
            raw_symbol = "terminal_exec"
        elif "error" in status_raw:
            event_type = "error"
            raw_symbol = "os_error"
        else:
            event_type = "tool_call"
            raw_symbol = action_name

        return RawTraceEvent(
            trace_id=trace_id,
            span_id=str(step_data.get("span_id") or f"span-{trace_id}-{seq_no}"),
            parent_span_id=step_data.get("parent_span_id"),
            agent_id=agent_id,
            role="desktop_operator",
            depth=0,
            event_type=event_type,
            raw_symbol=raw_symbol,
            canonical_symbol_candidate=raw_symbol,
            param_schema=params if isinstance(params, dict) else {"param_raw": str(params)},
            status=status,
            timestamp=step_data.get("timestamp") or base_timestamp,
            framework=self.framework,
            framework_schema_version=schema_version,
            sequence_no=seq_no,
            metadata={
                "action_type": action_type,
                "action": action_name,
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
osworld_adapter = OSWorldAdapter()
register_adapter(osworld_adapter)
