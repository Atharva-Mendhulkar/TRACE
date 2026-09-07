"""
Runtime Verification Engine & State Tracking (PRD §17, §16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

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


class HierarchicalRuntimeVerifier:
    """
    Hierarchical Runtime Verifier tracking parent and delegated child traces (PRD §16).
    Enforces bounded-depth recursion (MAX_DELEGATION_DEPTH=8), role-keyed child PDFA lookups,
    orphan span detection, and missing child trace warnings.
    """

    def __init__(
        self,
        parent_pdfa: PDFA,
        role_pdfas: Optional[Dict[str, PDFA]] = None,
        parent_policy_dfa: Optional[PolicyDFA] = None,
        role_policy_dfas: Optional[Dict[str, PolicyDFA]] = None,
        role_pdfa_resolver: Optional[Callable[[str], Optional[PDFA]]] = None,
        mode: Literal["gate", "observe"] = "observe",
        max_delegation_depth: int = 8,
        epsilon: float = 0.05,
        model_version: str = "v1",
        policy_version: Optional[str] = None,
    ):
        self.parent_pdfa = parent_pdfa
        self.role_pdfas = role_pdfas or {}
        self.parent_policy_dfa = parent_policy_dfa
        self.role_policy_dfas = role_policy_dfas or {}
        self.role_pdfa_resolver = role_pdfa_resolver
        self.mode = mode
        self.max_delegation_depth = max_delegation_depth
        self.epsilon = epsilon
        self.model_version = model_version
        self.policy_version = policy_version

        # Active delegations: parent_span_id -> dict(role, depth, child_spans, received_events)
        self.active_delegations: Dict[str, Dict[str, Any]] = {}
        # Known parent spans in system
        self.known_parent_spans: Set[str] = set()

        # Active ProductAutomaton per (trace_id, span_id)
        self.sessions: Dict[Tuple[str, str], ProductAutomaton] = {}
        # History per (trace_id, span_id)
        self.history: Dict[Tuple[str, str], List[str]] = {}

    def get_role_pdfa(self, role: str) -> Optional[PDFA]:
        if role in self.role_pdfas:
            return self.role_pdfas[role]
        if self.role_pdfa_resolver:
            pdfa = self.role_pdfa_resolver(role)
            if pdfa:
                self.role_pdfas[role] = pdfa
                return pdfa
        return None

    def register_role_pdfa(self, role: str, pdfa: PDFA, policy_dfa: Optional[PolicyDFA] = None) -> None:
        self.role_pdfas[role] = pdfa
        if policy_dfa:
            self.role_policy_dfas[role] = policy_dfa

    def verify_event(self, event: CESRecord) -> VerificationResponse:
        """Verify an event with hierarchical delegation context (PRD §16, §17)."""
        # 1. Check recursion depth limit (PRD §16.3)
        if event.depth > self.max_delegation_depth:
            explanation = CounterexampleExplainer.generate(
                event=event,
                classification=ClassificationResult(
                    symbol=event.symbol,
                    q_learned_before="q_root",
                    q_policy_before="p_root",
                    q_learned_after=None,
                    q_policy_after="p_sink",
                    is_structural=False,
                    is_statistical=False,
                    is_policy=True,
                ),
                pdfa=self.parent_pdfa,
                observed_prefix=self.history.get((event.trace_id, event.span_id), []),
                model_version=self.model_version,
                policy_version=self.policy_version,
            )
            explanation.classification = ["delegation_depth_exceeded"]
            return VerificationResponse(
                event_id=event.event_id,
                trace_id=event.trace_id,
                classification=["delegation_depth_exceeded"],
                violation=explanation,
                mode=self.mode,
                allowed=(self.mode != "gate"),
            )

        # 2. Track parent span registration
        if event.depth == 0:
            self.known_parent_spans.add(event.span_id)

        # 3. Check for orphan child trace (PRD §16.5)
        if event.depth > 0 or event.parent_span_id is not None:
            if not event.parent_span_id or event.parent_span_id not in self.known_parent_spans:
                explanation = CounterexampleExplainer.generate(
                    event=event,
                    classification=ClassificationResult(
                        symbol=event.symbol,
                        q_learned_before="q_orphan",
                        q_policy_before="p_orphan",
                        q_learned_after=None,
                        q_policy_after="p_sink",
                        is_structural=True,
                        is_statistical=False,
                        is_policy=False,
                    ),
                    pdfa=self.parent_pdfa,
                    observed_prefix=self.history.get((event.trace_id, event.span_id), []),
                    model_version=self.model_version,
                    policy_version=self.policy_version,
                )
                explanation.classification = ["orphan_child_span"]
                return VerificationResponse(
                    event_id=event.event_id,
                    trace_id=event.trace_id,
                    classification=["orphan_child_span"],
                    violation=explanation,
                    mode=self.mode,
                    allowed=(self.mode != "gate"),
                )
            else:
                # Valid child event
                if event.parent_span_id in self.active_delegations:
                    del_info = self.active_delegations[event.parent_span_id]
                    del_info["child_spans"].add(event.span_id)
                    del_info["received_events"] += 1

        # 4. Check if this is a delegation event in the parent stream
        is_delegate = (
            event.event_type == "delegate"
            or event.symbol.startswith("delegate(")
        )
        if is_delegate:
            role = event.role
            if not role and event.symbol.startswith("delegate(") and event.symbol.endswith(")"):
                role = event.symbol[9:-1]
            self.active_delegations[event.span_id] = {
                "role": role or "unknown",
                "depth": event.depth,
                "child_spans": set(),
                "received_events": 0,
            }
            self.known_parent_spans.add(event.span_id)

        # 5. Resolve target PDFA and Policy DFA
        if event.depth > 0 or event.parent_span_id:
            role = event.role
            if not role and event.parent_span_id in self.active_delegations:
                role = self.active_delegations[event.parent_span_id]["role"]
            target_pdfa = self.get_role_pdfa(role) if role else None
            target_policy = self.role_policy_dfas.get(role) if role else None
            # Fallback to parent pdfa if role model is not yet trained
            target_pdfa = target_pdfa or self.parent_pdfa
            target_policy = target_policy or self.parent_policy_dfa
        else:
            target_pdfa = self.parent_pdfa
            target_policy = self.parent_policy_dfa

        # 6. Step the session
        key = (event.trace_id, event.span_id)
        if key not in self.sessions:
            self.sessions[key] = ProductAutomaton(
                pdfa=target_pdfa,
                policy_dfa=target_policy,
                epsilon=self.epsilon,
            )
            self.history[key] = []

        session = self.sessions[key]
        history = self.history[key]

        classification = session.step(event.symbol)
        history.append(event.symbol)

        violation_explanation = None
        allowed = True

        if classification.is_violation:
            violation_explanation = CounterexampleExplainer.generate(
                event=event,
                classification=classification,
                pdfa=target_pdfa,
                policy_dfa=target_policy,
                observed_prefix=history,
                model_version=self.model_version,
                policy_version=self.policy_version,
            )
            if self.mode == "gate":
                allowed = False

        flags = list(classification.active_flags)

        # 7. Check for missing child traces upon parent termination (PRD §16.5)
        if event.depth == 0 and (event.event_type == "terminate" or event.symbol == "terminate"):
            for span_id, info in list(self.active_delegations.items()):
                if info.get("received_events", 0) == 0:
                    flags.append("missing_child_trace")

        return VerificationResponse(
            event_id=event.event_id,
            trace_id=event.trace_id,
            classification=flags,
            violation=violation_explanation,
            mode=self.mode,
            allowed=allowed,
            running_mean_nll=classification.running_mean_nll,
        )

    def replay_trace(self, events: List[CESRecord]) -> List[VerificationResponse]:
        """
        Replay a complete multi-span hierarchical trace in causal order.
        Orders by (timestamp, depth, sequence_no) to replay parent delegate before child events.
        """
        if not events:
            return []

        # Sort: parent events come before child events
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.depth, e.sequence_no))

        # Clear state
        self.sessions.clear()
        self.history.clear()
        self.active_delegations.clear()
        self.known_parent_spans.clear()

        # Pre-populate parent spans for robust linking
        for ev in sorted_events:
            if ev.depth == 0 or ev.event_type == "delegate" or ev.symbol.startswith("delegate("):
                self.known_parent_spans.add(ev.span_id)

        results: List[VerificationResponse] = []
        for ev in sorted_events:
            results.append(self.verify_event(ev))

        return results
