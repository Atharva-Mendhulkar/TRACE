"""
Synthetic Ground-Truth PDFA & Trace Generator with Controlled Noise (PRD §33.1, RQ2).
"""

from __future__ import annotations

import datetime
import json
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from trace.models.pdfa import PDFA


def create_ground_truth_research_agent_pdfa() -> PDFA:
    """
    Creates a canonical ground-truth PDFA modeling a web research agent:
    - q0: start state
    - q0 --[plan_step]--> q1
    - q1: stochastic branching:
        --[web_search (P=0.7)]--> q2
        --[file_read  (P=0.3)]--> q3
    - q2 --[tool_result]--> q4
    - q3 --[tool_result]--> q4
    - q4 --[memory_write]--> q5
    - q5: stochastic branching:
        --[plan_step (P=0.5)]--> q1
        --[terminate (P=0.5)]--> q_final
    """
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "plan_step", "q1", frequency=100)

    pdfa.add_transition("q1", "web_search", "q2", frequency=70)
    pdfa.add_transition("q1", "file_read", "q3", frequency=30)

    pdfa.add_transition("q2", "tool_result", "q4", frequency=70)
    pdfa.add_transition("q3", "tool_result", "q4", frequency=30)

    pdfa.add_transition("q4", "memory_write", "q5", frequency=100)

    pdfa.add_transition("q5", "plan_step", "q1", frequency=50)
    pdfa.add_transition("q5", "terminate", "q_final", frequency=50)
    pdfa.mark_final("q_final", count=50)

    pdfa.recompute_all_probabilities()
    return pdfa


class SyntheticTraceGenerator:
    """Generates synthetic traces from a ground-truth PDFA with controlled noise injection."""

    def __init__(self, ground_truth_pdfa: Optional[PDFA] = None, seed: int = 42):
        self.pdfa = ground_truth_pdfa or create_ground_truth_research_agent_pdfa()
        self.rng = random.Random(seed)

    def generate_trace(
        self,
        noise_rate: float = 0.0,
        anomalous_symbols: Optional[List[str]] = None,
        max_steps: int = 30,
    ) -> List[str]:
        """Generate a single trace following PDFA transition probabilities with noise injection."""
        anomalies = anomalous_symbols or ["shell_exec", "delete_record", "unexpected_call"]
        trace: List[str] = []
        curr_state = self.pdfa.q0

        for _ in range(max_steps):
            # Noise injection: randomly insert an anomaly or substitute
            if noise_rate > 0 and self.rng.random() < noise_rate:
                trace.append(self.rng.choice(anomalies))
                continue

            # Pick next transition by probability
            outgoing = [
                (sym, tgt, self.pdfa.P.get((curr_state, sym), 0.0))
                for (s, sym), tgt in self.pdfa.delta.items()
                if s == curr_state
            ]

            if not outgoing:
                break

            symbols, targets, probs = zip(*outgoing)
            # Normalize probs in case of rounding
            total_p = sum(probs)
            if total_p <= 0:
                break
            norm_probs = [p / total_p for p in probs]

            chosen_idx = self.rng.choices(range(len(symbols)), weights=norm_probs, k=1)[0]
            chosen_sym = symbols[chosen_idx]
            chosen_tgt = targets[chosen_idx]

            trace.append(chosen_sym)
            curr_state = chosen_tgt

            if curr_state in self.pdfa.F or chosen_sym == "terminate":
                break

        return trace

    def generate_corpus(
        self,
        num_traces: int = 100,
        noise_rate: float = 0.0,
    ) -> List[List[str]]:
        return [self.generate_trace(noise_rate=noise_rate) for _ in range(num_traces)]

    def generate_ces_events_json(
        self,
        num_traces: int = 20,
        agent_id: str = "synthetic-research-agent",
        noise_rate: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Generate full CES JSON records ready for ingestion."""
        events = []
        now = datetime.datetime.now(datetime.timezone.utc)

        for _ in range(num_traces):
            trace_id = str(uuid4())
            span_id = str(uuid4())
            syms = self.generate_trace(noise_rate=noise_rate)

            for seq, sym in enumerate(syms):
                ev_type = "tool_call"
                if sym == "terminate":
                    ev_type = "terminate"
                elif sym == "tool_result":
                    ev_type = "tool_result"
                elif sym == "plan_step":
                    ev_type = "plan_step"
                elif sym == "memory_write":
                    ev_type = "memory_write"

                ev_id = str(uuid4())
                t_str = (now + datetime.timedelta(seconds=seq)).isoformat()
                events.append({
                    "schema_version": "1.0",
                    "event_id": ev_id,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "agent_id": agent_id,
                    "depth": 0,
                    "event_type": ev_type,
                    "symbol": sym,
                    "raw_symbol": sym,
                    "attributes": {
                        "param_schema_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                        "status": "success",
                    },
                    "timestamp": t_str,
                    "framework": "custom",
                    "framework_schema_version": "1.0",
                    "adapter_version": "1.0.0",
                    "sequence_no": seq,
                    "status": "success",
                })

        return events


def run_rq2_benchmark(engines: Optional[List[str]] = None) -> Dict[str, Any]:
    """Execute synthetic-PDFA recovery experiment across engines and corpus sizes (PRD §33.1 RQ2)."""
    from trace.inference import get_learner_engine

    target_engines = engines or ["native-alergia", "native-edsm"]
    gt = create_ground_truth_research_agent_pdfa()
    gen = SyntheticTraceGenerator(gt, seed=123)

    test_corpus = gen.generate_corpus(num_traces=100, noise_rate=0.0)
    sizes = [20, 50, 100, 200]
    comparison_results = {}

    for eng_name in target_engines:
        engine_runs = []
        for sz in sizes:
            train_traces = gen.generate_corpus(num_traces=sz, noise_rate=0.0)
            engine = get_learner_engine(eng_name)
            learned_pdfa = engine.fit(train_traces)

            # Evaluate held-out likelihood
            nlls = []
            for t in test_corpus:
                m_nll, _, has_struct = learned_pdfa.compute_trace_mean_nll(t)
                if not has_struct and m_nll < float("inf"):
                    nlls.append(m_nll)

            avg_nll = sum(nlls) / len(nlls) if nlls else float("inf")
            engine_runs.append({
                "train_size": sz,
                "learned_states": len(learned_pdfa.states),
                "learned_transitions": len(learned_pdfa.delta),
                "held_out_mean_nll": avg_nll,
                "held_out_coverage": len(nlls) / len(test_corpus),
            })
        comparison_results[eng_name] = engine_runs

    return {"experiment": "RQ2_Learnability", "results": comparison_results}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run RQ2 Learnability Benchmark")
    parser.add_argument("--engines", nargs="+", default=["native-alergia", "native-edsm"], help="Engines to compare")
    args = parser.parse_args()
    res = run_rq2_benchmark(engines=args.engines)
    print(json.dumps(res, indent=2))
