"""
Ingestion Pipeline & Dead-Letter Handling (PRD §11.3, §10.5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from trace.adapters.base import get_adapter
from trace.schema.models import CESRecord, validate_ces_record


@dataclass
class IngestionResult:
    accepted: List[CESRecord] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.accepted) + len(self.rejected) + len(self.duplicates)


class IngestionPipeline:
    """Orchestrates framework adaptation, validation, and dead-letter routing."""

    def __init__(self, trace_store: Optional[Any] = None):
        self.trace_store = trace_store

    def ingest_event(
        self,
        raw_event: Dict[str, Any],
        framework: Optional[str] = None,
    ) -> IngestionResult:
        """Process a single raw framework event dictionary."""
        return self.ingest_events([raw_event], framework=framework)

    def ingest_events(
        self,
        raw_events: List[Dict[str, Any]],
        framework: Optional[str] = None,
    ) -> IngestionResult:
        """Process a batch of raw framework events."""
        result = IngestionResult()

        for idx, raw in enumerate(raw_events):
            # Check if this is already a valid CESRecord
            if "schema_version" in raw and raw.get("schema_version") == "1.0":
                val = validate_ces_record(raw)
                if val.is_valid:
                    try:
                        record = CESRecord(**raw)
                        self._handle_accepted_record(record, result)
                        continue
                    except Exception as e:
                        result.rejected.append({"index": idx, "raw_event": raw, "reason": str(e)})
                        continue
                else:
                    result.rejected.append({"index": idx, "raw_event": raw, "reason": val.errors})
                    continue

            # Detect framework
            fw = framework or raw.get("framework") or self._infer_framework(raw)
            if not fw:
                result.rejected.append({
                    "index": idx,
                    "raw_event": raw,
                    "reason": "Could not determine framework. Specify framework explicitly or include 'framework' in event."
                })
                continue

            adapter = get_adapter(fw)
            if not adapter:
                result.rejected.append({
                    "index": idx,
                    "raw_event": raw,
                    "reason": f"No registered adapter found for framework '{fw}'."
                })
                continue

            # Validate framework structure
            val_res = adapter.validate(raw)
            if not val_res.is_valid:
                result.rejected.append({
                    "index": idx,
                    "raw_event": raw,
                    "framework": fw,
                    "reason": f"Adapter validation failed: {val_res.errors}"
                })
                continue

            # Parse to RawTraceEvents
            try:
                parsed_events = adapter.parse(raw)
            except Exception as e:
                result.rejected.append({
                    "index": idx,
                    "raw_event": raw,
                    "framework": fw,
                    "reason": f"Adapter parse exception: {str(e)}"
                })
                continue

            # Convert to CESRecords and validate
            for p_event in parsed_events:
                try:
                    ces_record = adapter.to_ces(p_event)
                    ces_dict = ces_record.model_dump()
                    val = validate_ces_record(ces_dict)
                    if not val.is_valid:
                        result.rejected.append({
                            "index": idx,
                            "raw_event": raw,
                            "ces_record": ces_dict,
                            "reason": f"CES schema validation failed: {val.errors}"
                        })
                        continue

                    self._handle_accepted_record(ces_record, result)

                except Exception as e:
                    result.rejected.append({
                        "index": idx,
                        "raw_event": raw,
                        "reason": f"Conversion to CES failed: {str(e)}"
                    })

        return result

    def ingest_file(self, file_path: Union[str, Path], framework: Optional[str] = None) -> IngestionResult:
        """Read a JSON or JSONL file and ingest its events."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        events: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.startswith("["):
                events = json.loads(content)
            elif content.startswith("{"):
                # Could be single JSON object or JSONL
                try:
                    single = json.loads(content)
                    if isinstance(single, dict) and "events" in single and isinstance(single["events"], list):
                        events = single["events"]
                    else:
                        events = [single]
                except json.JSONDecodeError:
                    # Parse as JSON Lines
                    for line in content.splitlines():
                        if line.strip():
                            events.append(json.loads(line))
            else:
                for line in content.splitlines():
                    if line.strip():
                        events.append(json.loads(line))

        return self.ingest_events(events, framework=framework)

    def _handle_accepted_record(self, record: CESRecord, result: IngestionResult) -> None:
        if self.trace_store:
            # Idempotent store
            is_new = self.trace_store.write_event(record)
            if is_new:
                result.accepted.append(record)
            else:
                result.duplicates.append(record.event_id)
        else:
            result.accepted.append(record)

    def _infer_framework(self, raw: Dict[str, Any]) -> Optional[str]:
        if "jsonrpc" in raw or raw.get("method", "").startswith("tools/"):
            return "mcp"
        if "node" in raw or "checkpoint" in raw or "subgraph" in raw:
            return "langgraph"
        if "instance_id" in raw or ("action" in raw and "history" in raw):
            return "swebench"
        if "task_id" in raw or "action_type" in raw:
            return "osworld"
        return None
