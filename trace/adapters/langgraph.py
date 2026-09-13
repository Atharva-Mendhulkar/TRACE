"""
LangGraph Framework Adapter (PRD §11.6).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List
from uuid import uuid4

from trace.adapters.base import FrameworkAdapter, RawTraceEvent, register_adapter
from trace.schema.models import ValidationResult


class LangGraphAdapter(FrameworkAdapter):
    """Adapter for LangGraph state graph node/edge execution events."""

    framework = "langgraph"
    adapter_version = "1.0.0"
    supported_framework_schema_versions = ["0.1", "0.2", "latest"]

    def detect_schema_version(self, raw_event: Dict[str, Any]) -> str:
        version = raw_event.get("langgraph_version") or raw_event.get("version")
        if version and version in self.supported_framework_schema_versions:
            return str(version)
        if "node" in raw_event or "checkpoint" in raw_event or "step" in raw_event:
            return "0.2"
        return "unknown"

    def validate(self, raw_event: Dict[str, Any]) -> ValidationResult:
        errors = []
        if "node" not in raw_event and "event" not in raw_event and "step" not in raw_event:
            errors.append("Missing LangGraph node or event definition")
        if "trace_id" not in raw_event and "run_id" not in raw_event and "thread_id" not in raw_event:
            errors.append("Missing trace/run identifier")
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def parse(self, raw_event: Dict[str, Any]) -> List[RawTraceEvent]:
        events: List[RawTraceEvent] = []
        trace_id = str(raw_event.get("trace_id") or raw_event.get("run_id") or raw_event.get("thread_id") or uuid4())
        span_id = str(raw_event.get("span_id") or f"span-{trace_id}")
        parent_span_id = raw_event.get("parent_span_id")
        agent_id = str(raw_event.get("agent_id") or "langgraph-agent")
        role = raw_event.get("role")
        depth = int(raw_event.get("depth", 0))
        timestamp = raw_event.get("timestamp") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        seq_no = int(raw_event.get("sequence_no") or raw_event.get("step", 0))
        schema_version = self.detect_schema_version(raw_event)

        node = raw_event.get("node", "")
        event_kind = raw_event.get("event") or raw_event.get("type", "")

        # 1. Termination: node is __end__ or event is terminate
        if node == "__end__" or event_kind in ("on_chain_end", "terminate", "graph_end"):
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
                    framework="langgraph",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no                )
            )

        # 2. Delegation to sub-graph
        elif node.startswith("subgraph_") or event_kind in ("delegate", "subgraph_invoke"):
            target_role = raw_event.get("subgraph_name") or node.replace("subgraph_", "")
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=target_role,
                    depth=depth,
                    event_type="delegate",
                    raw_symbol=f"delegate({target_role})",
                    canonical_symbol_candidate=f"delegate({target_role})",
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="langgraph",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no                )
            )

        # 3. Tool execution node
        elif node == "tools" or event_kind in ("on_tool_start", "tool_call"):
            tool_name = raw_event.get("tool") or raw_event.get("name") or "tool"
            args = raw_event.get("input") or raw_event.get("args") or {}
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
                    status=raw_event.get("status", "success"),
                    timestamp=timestamp,
                    framework="langgraph",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no                )
            )

        # 4. Error events
        elif event_kind in ("on_chain_error", "error", "tool_error"):
            events.append(
                RawTraceEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=agent_id,
                    role=role,
                    depth=depth,
                    event_type="error",
                    raw_symbol=f"{node}_error" if node else "error",
                    param_schema={},
                    status="failure",
                    error_class=raw_event.get("error_class", "graph_error"),
                    retryable_error=raw_event.get("retryable", False),
                    timestamp=timestamp,
                    framework="langgraph",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no                )
            )

        # 5. Planning / General Graph Node Step
        else:
            step_name = node or raw_event.get("name") or "step"
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
                    canonical_symbol_candidate=step_name,
                    param_schema={},
                    status="success",
                    timestamp=timestamp,
                    framework="langgraph",
                    framework_schema_version=schema_version,
                    sequence_no=seq_no                )
            )

        return events


# Register singleton
register_adapter(LangGraphAdapter())
