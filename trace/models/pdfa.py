"""
Probabilistic Deterministic Finite Automaton (PDFA) Formal Model (PRD §15).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class PDFATransition:
    from_state: str
    symbol: str
    to_state: str
    frequency: int
    probability: float
    low_confidence: bool = False


class PDFA:
    """Probabilistic Deterministic Finite Automaton A = (Q, Sigma, delta, q0, P, F)."""

    def __init__(self, q0: str = "q0", alphabet: Optional[Set[str]] = None):
        self.q0 = q0
        self.states: Set[str] = {q0}
        self.alphabet: Set[str] = set(alphabet) if alphabet else set()
        # delta: (state, symbol) -> next_state
        self.delta: Dict[Tuple[str, str], str] = {}
        # P: (state, symbol) -> probability in [0, 1]
        self.P: Dict[Tuple[str, str], float] = {}
        # F: accepting/final states (PRD §15.6)
        self.F: Set[str] = set()
        # Transition frequency counts: (state, symbol) -> count
        self.counts: Dict[Tuple[str, str], int] = {}
        # Outgoing total count per state: state -> total count
        self.state_totals: Dict[str, int] = {q0: 0}
        # Termination counts per state: state -> count
        self.final_counts: Dict[str, int] = {q0: 0}

    def add_state(self, state: str, is_final: bool = False) -> None:
        self.states.add(state)
        if state not in self.state_totals:
            self.state_totals[state] = 0
            self.final_counts[state] = 0
        if is_final:
            self.F.add(state)

    def add_transition(
        self,
        from_state: str,
        symbol: str,
        to_state: str,
        frequency: int = 1,
        recompute_probs: bool = True,
    ) -> None:
        """Add or update a deterministic transition."""
        self.add_state(from_state)
        self.add_state(to_state)
        self.alphabet.add(symbol)

        key = (from_state, symbol)
        prev_count = self.counts.get(key, 0)
        self.counts[key] = prev_count + frequency
        self.delta[key] = to_state
        self.state_totals[from_state] = self.state_totals.get(from_state, 0) + frequency

        if recompute_probs:
            self.recompute_state_probabilities(from_state)

    def mark_final(self, state: str, count: int = 1) -> None:
        """Record a termination event at state (PRD §15.6)."""
        self.add_state(state, is_final=True)
        self.final_counts[state] = self.final_counts.get(state, 0) + count

    def recompute_state_probabilities(self, state: str) -> None:
        """Recompute maximum-likelihood probabilities for outgoing transitions from a state."""
        total = self.state_totals.get(state, 0)
        if total == 0:
            return

        for (s, sym), count in self.counts.items():
            if s == state:
                prob = count / total
                self.P[(s, sym)] = prob

    def recompute_all_probabilities(self) -> None:
        """Recompute probabilities across all states."""
        for state in self.states:
            self.recompute_state_probabilities(state)

    def step(self, current_state: str, symbol: str) -> Optional[Tuple[str, float]]:
        """Advance one step deterministically. Returns (next_state, probability) or None if undefined."""
        key = (current_state, symbol)
        if key not in self.delta:
            return None
        next_state = self.delta[key]
        prob = self.P.get(key, 0.0)
        return next_state, prob

    def get_outgoing_symbols(self, state: str) -> Set[str]:
        """Return set of defined symbols from state."""
        return {sym for (s, sym) in self.delta.keys() if s == state}

    def compute_trace_mean_nll(
        self, symbols: List[str]
    ) -> Tuple[float, List[Optional[float]], bool]:
        """
        Compute mean per-event Negative Log-Likelihood (PRD §15.7).
        Returns (mean_nll, per_event_nlls, has_structural_anomaly).
        """
        if not symbols:
            return 0.0, [], False

        curr_state = self.q0
        nlls: List[Optional[float]] = []
        has_structural = False
        valid_nlls: List[float] = []

        for sym in symbols:
            res = self.step(curr_state, sym)
            if res is None:
                has_structural = True
                nlls.append(None)
                # After structural anomaly, we can't deterministically advance without recovery
            else:
                next_st, prob = res
                curr_state = next_st
                if prob > 0:
                    nll = -math.log(prob)
                else:
                    nll = 50.0  # Large penalty for zero probability
                nlls.append(nll)
                valid_nlls.append(nll)

        mean_nll = sum(valid_nlls) / len(valid_nlls) if valid_nlls else float("inf")
        return mean_nll, nlls, has_structural

    def to_dict(self) -> Dict[str, Any]:
        """Serialize PDFA to JSON-compatible dictionary."""
        transitions_list = []
        for (f_state, sym), t_state in self.delta.items():
            cnt = self.counts.get((f_state, sym), 0)
            prob = self.P.get((f_state, sym), 0.0)
            transitions_list.append({
                "from_state": f_state,
                "symbol": sym,
                "to_state": t_state,
                "frequency": cnt,
                "probability": prob,
                "low_confidence": cnt < 3,
            })

        return {
            "q0": self.q0,
            "states": sorted(list(self.states)),
            "alphabet": sorted(list(self.alphabet)),
            "final_states": sorted(list(self.F)),
            "transitions": transitions_list,
            "final_counts": self.final_counts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PDFA:
        """Construct PDFA from dictionary."""
        pdfa = cls(q0=data.get("q0", "q0"), alphabet=set(data.get("alphabet", [])))
        for state in data.get("states", []):
            pdfa.add_state(state)
        for f_state in data.get("final_states", []):
            pdfa.F.add(f_state)

        for trans in data.get("transitions", []):
            f_st = trans["from_state"]
            sym = trans["symbol"]
            t_st = trans["to_state"]
            freq = trans.get("frequency", 1)
            pdfa.add_transition(f_st, sym, t_st, frequency=freq, recompute_probs=False)
            if "probability" in trans:
                pdfa.P[(f_st, sym)] = float(trans["probability"])

        if "final_counts" in data:
            pdfa.final_counts = data["final_counts"]

        pdfa.recompute_all_probabilities()
        return pdfa

    def to_dot(self) -> str:
        """Export automaton as Graphviz DOT string."""
        lines = ["digraph PDFA {", "  rankdir=LR;", "  node [shape=circle];"]
        lines.append(f'  "{self.q0}" [style=bold];')
        for f_st in self.F:
            lines.append(f'  "{f_st}" [shape=doublecircle];')

        for (s, sym), t in self.delta.items():
            prob = self.P.get((s, sym), 0.0)
            freq = self.counts.get((s, sym), 0)
            label = f"{sym}\\nP={prob:.2f} (n={freq})"
            lines.append(f'  "{s}" -> "{t}" [label="{label}"];')

        lines.append("}")
        return "\n".join(lines)
