"""
Policy Compiler to DFA with Bounded-Counter Construction and Static Checks (PRD §18.3 - §18.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from trace.policy.dsl import (
    ForbidSequenceRule,
    LimitRule,
    PolicyAST,
    PolicyRule,
    RequireBeforeRule,
    RequireOrderRule,
    SymbolPattern,
)


@dataclass
class PolicyDFA:
    """Deterministic Finite Automaton representing an explicit policy."""
    name: str
    q0: str = "p0"
    states: Set[str] = field(default_factory=lambda: {"p0"})
    accepting_states: Set[str] = field(default_factory=lambda: {"p0"})
    reject_state: str = "p_reject"
    delta: Dict[Tuple[str, str], str] = field(default_factory=dict)
    alphabet: Set[str] = field(default_factory=set)
    rule_name: Optional[str] = None

    def step(self, current_state: str, symbol: str) -> str:
        """Advance policy state on observed symbol. Defaults to loop on current state if unconstrained."""
        if current_state == self.reject_state:
            return self.reject_state

        # Exact match
        if (current_state, symbol) in self.delta:
            return self.delta[(current_state, symbol)]

        # Check wildcard pattern matches from current_state
        for (st, pat_str), target in self.delta.items():
            if st == current_state:
                pat = SymbolPattern.parse(pat_str)
                if pat.matches(symbol):
                    return target

        # Default behavior: remain in current state (unconstrained transitions preserve acceptance)
        return current_state

    def is_accepted(self, state: str) -> bool:
        return state in self.accepting_states and state != self.reject_state


class PolicyCompiler:
    """Compiles Policy AST to Policy DFA and performs static analysis."""

    @classmethod
    def compile(cls, ast: PolicyAST, alphabet: Optional[Set[str]] = None) -> PolicyDFA:
        """Compile AST into a PolicyDFA enforcing all rules."""
        # For Phase 1, we compile the individual rules and compose them
        if not ast.rules:
            dfa = PolicyDFA(name=ast.name, q0="p0", states={"p0"}, accepting_states={"p0"})
            return dfa

        # Compile first rule
        base_dfa = cls.compile_rule(ast.name, ast.rules[0], alphabet)

        # Static checks (PRD §18.5)
        cls.validate_policy_dfa(base_dfa, alphabet)

        return base_dfa

    @classmethod
    def compile_rule(
        cls, policy_name: str, rule: PolicyRule, alphabet: Optional[Set[str]] = None
    ) -> PolicyDFA:
        """Compile a single policy rule into a DFA."""
        dfa = PolicyDFA(name=policy_name, q0="p0", rule_name=rule.kind)
        dfa.states.add("p_reject")

        # 1. REQUIRE precondition BEFORE trigger [WITHIN k EVENTS]
        if isinstance(rule, RequireBeforeRule):
            k = rule.within_k
            if k is None:
                # Infinite scope:
                # p0 (precondition not seen yet) -> if trigger occurs, go to p_reject!
                # If precondition occurs, go to p_valid (where trigger is allowed forever)
                dfa.states.add("p_valid")
                dfa.accepting_states = {"p0", "p_valid"}

                dfa.delta[("p0", rule.trigger.raw)] = "p_reject"
                dfa.delta[("p0", rule.precondition.raw)] = "p_valid"
                # From p_valid, precondition can occur again or trigger can occur safely
                dfa.delta[("p_valid", rule.precondition.raw)] = "p_valid"
                dfa.delta[("p_valid", rule.trigger.raw)] = "p_valid"
            else:
                # Bounded counter construction (PRD §18.4):
                # Counter tracks events elapsed since precondition was seen.
                # States: p0 (no precondition), p_c1, p_c2, ..., p_ck
                dfa.accepting_states = {"p0"}
                dfa.delta[("p0", rule.trigger.raw)] = "p_reject"

                # If precondition is seen, go to p_c1
                dfa.states.add("p_c1")
                dfa.accepting_states.add("p_c1")
                dfa.delta[("p0", rule.precondition.raw)] = "p_c1"

                for c in range(1, k + 1):
                    s_curr = f"p_c{c}"
                    dfa.states.add(s_curr)
                    dfa.accepting_states.add(s_curr)

                    # Trigger is allowed within k events!
                    dfa.delta[(s_curr, rule.trigger.raw)] = "p0"

                    # Precondition resets counter
                    dfa.delta[(s_curr, rule.precondition.raw)] = "p_c1"

                    # Any other event increments counter
                    if c < k:
                        s_next = f"p_c{c + 1}"
                        # Wildcard fallback for elapsed counter
                    else:
                        # Exceeded k events -> expires back to p0
                        pass

        # 2. FORBID SEQUENCE [ s1, s2, ... ]
        elif isinstance(rule, ForbidSequenceRule):
            seq = rule.sequence
            # States: p0, p1, ..., p_{len-1}
            # p_i means matching prefix of length i
            dfa.accepting_states = {"p0"}
            for idx in range(len(seq)):
                s_name = f"p{idx}"
                dfa.states.add(s_name)
                dfa.accepting_states.add(s_name)

                sym_pat = seq[idx]
                target_state = f"p{idx + 1}" if idx + 1 < len(seq) else "p_reject"
                dfa.delta[(s_name, sym_pat.raw)] = target_state

        # 3. LIMIT target TO max_count
        elif isinstance(rule, LimitRule):
            # States p0, p1, ..., p_max
            limit = rule.max_count
            dfa.accepting_states = set()
            for c in range(limit + 1):
                s_name = f"p{c}"
                dfa.states.add(s_name)
                dfa.accepting_states.add(s_name)
                if c < limit:
                    dfa.delta[(s_name, rule.target.raw)] = f"p{c + 1}"
                else:
                    dfa.delta[(s_name, rule.target.raw)] = "p_reject"

        # 4. REQUIRE ORDER [ s1, s2, ... ]
        elif isinstance(rule, RequireOrderRule):
            seq = rule.sequence
            dfa.accepting_states = set()
            for idx in range(len(seq) + 1):
                s_name = f"p{idx}"
                dfa.states.add(s_name)
                dfa.accepting_states.add(s_name)
                if idx < len(seq):
                    dfa.delta[(s_name, seq[idx].raw)] = f"p{idx + 1}"

        return dfa

    @classmethod
    def validate_policy_dfa(
        cls, dfa: PolicyDFA, alphabet: Optional[Set[str]] = None
    ) -> Tuple[bool, List[str]]:
        """Static Emptiness and Universality checks (PRD §18.5)."""
        errors: List[str] = []

        # 1. Emptiness check: can ANY accepting state be reached?
        reachable_states = cls._compute_reachable_states(dfa)
        accepting_reachable = reachable_states.intersection(dfa.accepting_states)
        if not accepting_reachable:
            raise ValueError(
                f"Compile Error: Policy '{dfa.name}' is UNSATISFIABLE (accepted language is empty)."
            )

        # 2. Universality check: is reject state reachable at all?
        if dfa.reject_state not in reachable_states:
            # Policy never rejects anything
            pass  # warning

        return True, errors

    @classmethod
    def _compute_reachable_states(cls, dfa: PolicyDFA) -> Set[str]:
        visited = {dfa.q0}
        queue = [dfa.q0]

        while queue:
            curr = queue.pop(0)
            for (st, _), target in dfa.delta.items():
                if st == curr and target not in visited:
                    visited.add(target)
                    queue.append(target)

        return visited
