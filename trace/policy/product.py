"""
Product Automaton & Multi-Track Conformance Classification (PRD §19).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from trace.models.pdfa import PDFA
from trace.policy.compiler import PolicyDFA


@dataclass
class ClassificationResult:
    """Multi-track conformance classification result (PRD §17.2, §19.2, §20)."""
    symbol: str
    q_learned_before: str
    q_policy_before: str
    q_learned_after: Optional[str]
    q_policy_after: str
    is_structural: bool
    is_statistical: bool
    is_policy: bool
    probability: Optional[float] = None
    nll: Optional[float] = None
    running_mean_nll: float = 0.0

    @property
    def active_flags(self) -> List[str]:
        flags = []
        if self.is_policy:
            flags.append("policy")
        if self.is_structural:
            flags.append("structural")
        if self.is_statistical:
            flags.append("statistical")
        return flags

    @property
    def is_violation(self) -> bool:
        return bool(self.active_flags)


class ProductAutomaton:
    """
    Jointly tracks (q_learned, q_policy) control-state pair plus parallel NLL likelihood signal
    in accordance with PRD §19.1 / ADR-005.
    """

    def __init__(
        self,
        pdfa: PDFA,
        policy_dfa: Optional[PolicyDFA] = None,
        epsilon: float = 0.05,
    ):
        self.pdfa = pdfa
        self.policy_dfa = policy_dfa or PolicyDFA(name="default_allow", q0="p0", accepting_states={"p0"})
        self.epsilon = epsilon

        # State tracking
        self.q_learned: str = self.pdfa.q0
        self.q_policy: str = self.policy_dfa.q0
        self.last_known_good_learned: str = self.pdfa.q0
        self.last_known_good_policy: str = self.policy_dfa.q0

        # Parallel scalar likelihood tracking
        self.total_nll: float = 0.0
        self.valid_event_count: int = 0
        self.event_count: int = 0

    @property
    def running_mean_nll(self) -> float:
        if self.valid_event_count == 0:
            return 0.0
        return self.total_nll / self.valid_event_count

    def step(self, symbol: str) -> ClassificationResult:
        """
        Advance product state by one observed symbol (PRD §19.2).
        Returns classification result with all active violation flags.
        """
        self.event_count += 1
        q_l_before = self.q_learned
        q_p_before = self.q_policy

        # 1. Evaluate Learned PDFA transition
        res = self.pdfa.step(self.q_learned, symbol)
        is_structural = False
        is_statistical = False
        prob: Optional[float] = None
        step_nll: Optional[float] = None
        next_q_l: Optional[str] = None

        if res is None:
            is_structural = True
            # For explanation purposes, retain last known-good state
            next_q_l = self.q_learned
        else:
            next_q_l, prob = res
            self.q_learned = next_q_l
            if prob < self.epsilon:
                is_statistical = True

            if prob > 0:
                step_nll = -math.log(prob)
                self.total_nll += step_nll
                self.valid_event_count += 1

        # 2. Evaluate Policy DFA transition
        next_q_p = self.policy_dfa.step(self.q_policy, symbol)
        self.q_policy = next_q_p
        is_policy = not self.policy_dfa.is_accepted(next_q_p)

        # Update last known-good states if this step was completely clean
        if not is_structural and not is_statistical and not is_policy:
            self.last_known_good_learned = self.q_learned
            self.last_known_good_policy = self.q_policy

        return ClassificationResult(
            symbol=symbol,
            q_learned_before=q_l_before,
            q_policy_before=q_p_before,
            q_learned_after=next_q_l,
            q_policy_after=next_q_p,
            is_structural=is_structural,
            is_statistical=is_statistical,
            is_policy=is_policy,
            probability=prob,
            nll=step_nll,
            running_mean_nll=self.running_mean_nll,
        )

    def reset(self) -> None:
        """Reset product automaton to initial states."""
        self.q_learned = self.pdfa.q0
        self.q_policy = self.policy_dfa.q0
        self.last_known_good_learned = self.pdfa.q0
        self.last_known_good_policy = self.policy_dfa.q0
        self.total_nll = 0.0
        self.valid_event_count = 0
        self.event_count = 0
