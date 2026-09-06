"""
Runtime Verification Engine & State Tracking (PRD §17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from trace.explainability.explainer import CounterexampleExplainer, CounterexampleExplanation
from trace.models.pdfa import PDFA
from trace.policy.compiler import PolicyDFA
from trace.policy.product import ClassificationResult, ProductAutomaton
from trace.schema.models import CESRecord


@dataclass
class VerificationResponse:
    event_id: str
    trace_id: str
    classification: List[str]
    violation: Optional[CounterexampleExplanation] = None
    mode: Literal["gate", "observe"] = "observe"
    allowed: bool = True
    running_mean_nll: float = 0.0


class RuntimeVerifier:
    """Performs streaming and replay runtime verification against PDFA and Policy DFA."""

    def __init__(
        self,
        pdfa: PDFA,
        policy_dfa: Optional[PolicyDFA] = None,
        mode: Literal["gate", "observe"] = "observe",
        epsilon: float = 0.05,
        model_version: str = "v1",
        policy_version: Optional[str] = None,
    ):
        self.pdfa = pdfa
        self.policy_dfa = policy_dfa
        self.mode = mode
        self.epsilon = epsilon
        self.model_version = model_version
        self.policy_version = policy_version

        # Active sessions: (trace_id, span_id) -> ProductAutomaton
        self.sessions: Dict[Tuple[str, str], ProductAutomaton] = {}
        # History of observed symbols: (trace_id, span_id) -> List[str]
        self.history: Dict[Tuple[str, str], List[str]] = {}

    def get_or_create_session(self, trace_id: str, span_id: str) -> ProductAutomaton:
        key = (trace_id, span_id)
        if key not in self.sessions:
            self.sessions[key] = ProductAutomaton(
                pdfa=self.pdfa,
                policy_dfa=self.policy_dfa,
                epsilon=self.epsilon,
            )
            self.history[key] = []
        return self.sessions[key]

    def verify_event(self, event: CESRecord) -> VerificationResponse:
        """Verify a single incoming execution event."""
        session = self.get_or_create_session(event.trace_id, event.span_id)
        history = self.history[(event.trace_id, event.span_id)]

        # Step product automaton
        classification = session.step(event.symbol)
        history.append(event.symbol)

        violation_explanation = None
        allowed = True

        if classification.is_violation:
            violation_explanation = CounterexampleExplainer.generate(
                event=event,
                classification=classification,
                pdfa=self.pdfa,
                policy_dfa=self.policy_dfa,
                observed_prefix=history,
                model_version=self.model_version,
                policy_version=self.policy_version,
            )

            # In gate mode, violations block execution (PRD §17.4)
            if self.mode == "gate":
                allowed = False

        return VerificationResponse(
            event_id=event.event_id,
            trace_id=event.trace_id,
            classification=classification.active_flags,
            violation=violation_explanation,
            mode=self.mode,
            allowed=allowed,
            running_mean_nll=classification.running_mean_nll,
        )

    def replay_trace(self, events: List[CESRecord]) -> List[VerificationResponse]:
        """
        Replay a complete ordered event trace (PRD §17.5).
        Resets session state first to ensure deterministic replay.
        """
        if not events:
            return []

        # Sort events by sequence_no
        sorted_events = sorted(events, key=lambda e: e.sequence_no)
        trace_id = sorted_events[0].trace_id
        span_id = sorted_events[0].span_id

        # Clean session
        key = (trace_id, span_id)
        self.sessions.pop(key, None)
        self.history.pop(key, None)

        results: List[VerificationResponse] = []
        for ev in sorted_events:
            results.append(self.verify_event(ev))

        return results
