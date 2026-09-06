"""
Explainability & Counterexample Generation (PRD §21).
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

from trace.models.pdfa import PDFA
from trace.policy.compiler import PolicyDFA
from trace.policy.product import ClassificationResult
from trace.schema.models import CESRecord


@dataclass
class OffendingAction:
    framework_native_name: str
    canonical_symbol: str
    event_type: str


@dataclass
class PreviousKnownGoodState:
    state_id: str
    reached_via_symbol: Optional[str] = None


@dataclass
class DelegationContext:
    depth: int = 0
    parent_span_id: Optional[str] = None
    role: Optional[str] = None


@dataclass
class CounterexampleExplanation:
    violation_id: str
    trace_id: str
    event_id: str
    classification: List[str]
    offending_action: OffendingAction
    previous_known_good_state: PreviousKnownGoodState
    expected_symbols_at_state: List[str]
    observed_symbol: str
    probability_if_applicable: Optional[float]
    policy_rule_if_applicable: Optional[str]
    shortest_offending_suffix: List[str]
    delegation_context: DelegationContext
    model_version: str
    policy_version: Optional[str]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CounterexampleExplainer:
    """Generates human-readable, schema-valid counterexample explanations (PRD §21.2)."""

    @classmethod
    def generate(
        cls,
        event: CESRecord,
        classification: ClassificationResult,
        pdfa: PDFA,
        policy_dfa: Optional[PolicyDFA] = None,
        observed_prefix: Optional[List[str]] = None,
        model_version: str = "v1",
        policy_version: Optional[str] = None,
    ) -> CounterexampleExplanation:
        """Construct counterexample JSON matching PRD §21.1."""
        prev_st = classification.q_learned_before
        expected_symbols = sorted(list(pdfa.get_outgoing_symbols(prev_st)))

        # Shortest offending suffix
        if observed_prefix and len(observed_prefix) >= 2:
            shortest_suffix = [observed_prefix[-2], event.symbol]
        else:
            shortest_suffix = [event.symbol]

        policy_rule = None
        if classification.is_policy and policy_dfa:
            policy_rule = policy_dfa.name or policy_dfa.rule_name

        reached_via = None
        if observed_prefix and len(observed_prefix) >= 2:
            reached_via = observed_prefix[-2]

        explanation = CounterexampleExplanation(
            violation_id=str(uuid4()),
            trace_id=event.trace_id,
            event_id=event.event_id,
            classification=classification.active_flags,
            offending_action=OffendingAction(
                framework_native_name=event.raw_symbol,
                canonical_symbol=event.symbol,
                event_type=event.event_type,
            ),
            previous_known_good_state=PreviousKnownGoodState(
                state_id=prev_st,
                reached_via_symbol=reached_via,
            ),
            expected_symbols_at_state=expected_symbols,
            observed_symbol=event.symbol,
            probability_if_applicable=classification.probability,
            policy_rule_if_applicable=policy_rule,
            shortest_offending_suffix=shortest_suffix,
            delegation_context=DelegationContext(
                depth=event.depth,
                parent_span_id=event.parent_span_id,
                role=event.role,
            ),
            model_version=model_version,
            policy_version=policy_version,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        return explanation
