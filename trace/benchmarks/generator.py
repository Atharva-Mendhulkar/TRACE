"""
Synthetic & Realistic Agent Benchmark Trace Generators (PRD §32).
Simulates agent behavior distributions for SWE-bench and autonomous workflow research.
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple
from uuid import uuid4

from trace.schema.models import CESRecord, EventAttributes, ProvenanceInfo


class BenchmarkTraceGenerator:
    """Generates parameterized benchmark corpora for empirical evaluation (RQ1–RQ6)."""

    def __init__(self, seed: int = 42):
        random.seed(seed)

    def generate_normal_corpus(self, num_traces: int = 100) -> List[List[str]]:
        """
        Generate normal execution traces for a Software Engineering agent.
        Typical workflow: plan -> (read | search)* -> edit -> test -> (commit)? -> terminate
        """
        corpus: List[List[str]] = []
        for _ in range(num_traces):
            trace = ["plan"]
            # Research phase
            research_steps = random.choices(
                ["file_read", "web_search", "list_dir"],
                weights=[0.6, 0.3, 0.1],
                k=random.randint(1, 3),
            )
            trace.extend(research_steps)

            # Implementation phase
            trace.append("file_edit")
            trace.append("run_test")

            # Optional commit
            if random.random() > 0.3:
                trace.append("git_commit")

            trace.append("terminate")
            corpus.append(trace)
        return corpus

    def generate_evaluation_set(
        self, num_normal: int = 50, num_anomalous: int = 50
    ) -> List[Tuple[List[str], bool, str]]:
        """
        Generate mixed evaluation set returning tuples of (trace, is_anomaly, anomaly_type).
        """
        eval_set: List[Tuple[List[str], bool, str]] = []

        # Normal traces
        normal_traces = self.generate_normal_corpus(num_normal)
        for t in normal_traces:
            eval_set.append((t, False, "normal"))

        # Anomalous traces
        for _ in range(num_anomalous):
            anomaly_type = random.choice(["structural", "statistical", "policy_forbidden"])
            if anomaly_type == "structural":
                # Inject illegal or unseen action
                t = ["plan", "file_read", "drop_production_db", "terminate"]
                eval_set.append((t, True, "structural"))
            elif anomaly_type == "statistical":
                # Statistically rare sequence of retries
                t = ["plan", "file_edit", "run_test", "run_test", "run_test", "run_test", "terminate"]
                eval_set.append((t, True, "statistical"))
            else:
                # Direct policy violation: file_edit without prior file_read
                t = ["plan", "git_commit", "terminate"]
                eval_set.append((t, True, "policy_forbidden"))

        random.shuffle(eval_set)
        return eval_set

    def to_ces_records(self, symbols: List[str], agent_id: str = "bench-agent") -> List[CESRecord]:
        """Convert a sequence of symbols into full CESRecord objects."""
        trace_id = str(uuid4())
        span_id = f"span-{trace_id[:8]}"
        records = []

        for i, sym in enumerate(symbols):
            ev_type = "terminate" if sym == "terminate" else "tool_call"
            records.append(
                CESRecord(
                    schema_version="1.0",
                    event_id=str(uuid4()),
                    trace_id=trace_id,
                    span_id=span_id,
                    agent_id=agent_id,
                    event_type=ev_type,
                    symbol=sym,
                    raw_symbol=sym,
                    attributes=EventAttributes(param_schema_hash="hash-bench", status="success"),
                    timestamp="2026-09-07T12:00:00Z",
                    framework="mcp",
                    framework_schema_version="1.0",
                    adapter_version="1.0.0",
                    sequence_no=i,
                    status="success",
                    provenance=ProvenanceInfo(timestamp_source="framework", ingested_at="2026-09-07T12:00:00Z"),
                )
            )
        return records
