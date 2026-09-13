"""
Native State-Merging Learners: ALERGIA/MDI and EDSM (PRD §14.3).
"""

from __future__ import annotations

import math
from typing import Set, Tuple

from trace.inference.pta import PrefixTreeAcceptor
from trace.models.pdfa import PDFA


class NativeALERGIAEngine:
    """
    Pure ALERGIA/MDI probabilistic state-merging with Hoeffding-bound compatibility.
    """

    name = "native-alergia"

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha

    def fit(self, traces) -> PDFA:
        if not traces:
            return PDFA(q0="q0")
        pdfa = PrefixTreeAcceptor.build(traces)
        while True:
            states = sorted(
                pdfa.states,
                key=lambda s: pdfa.state_totals.get(s, 0) + pdfa.final_counts.get(s, 0),
                reverse=True,
            )
            best_pair, best_score = None, -1.0
            for i, q1 in enumerate(states):
                for q2 in states[i + 1 :]:
                    compat, score = self._test_compatibility(pdfa, q1, q2)
                    if compat and score > best_score:
                        best_pair, best_score = (q1, q2), score
            if best_pair is None:
                break
            pdfa.merge_state(*best_pair)
        pdfa.recompute_all_probabilities()
        return pdfa.canonicalize()

    def _test_compatibility(
        self, pdfa: PDFA, q1: str, q2: str, checked: Set[Tuple[str, str]] | None = None
    ) -> Tuple[bool, float]:
        if checked is None:
            checked = set()
        pair = (min(q1, q2), max(q1, q2))
        if pair in checked:
            return True, 0.0
        checked.add(pair)

        n1 = pdfa.state_totals.get(q1, 0) + pdfa.final_counts.get(q1, 0)
        n2 = pdfa.state_totals.get(q2, 0) + pdfa.final_counts.get(q2, 0)
        if n1 == 0 or n2 == 0:
            return False, 0.0

        q1_syms, q2_syms = pdfa.get_outgoing_symbols(q1), pdfa.get_outgoing_symbols(q2)
        common = q1_syms & q2_syms
        q1_final = pdfa.final_counts.get(q1, 0) > 0
        q2_final = pdfa.final_counts.get(q2, 0) > 0

        if not common and not (q1_final and q2_final):
            return False, 0.0
        if q1_final != q2_final and n1 > 1 and n2 > 1:
            return False, 0.0

        eps1 = math.sqrt((1.0 / (2.0 * n1)) * math.log(2.0 / self.alpha))
        eps2 = math.sqrt((1.0 / (2.0 * n2)) * math.log(2.0 / self.alpha))
        bound = min(0.8, eps1 + eps2)

        term_diff = abs(
            (pdfa.final_counts.get(q1, 0) / n1) - (pdfa.final_counts.get(q2, 0) / n2)
        )
        if term_diff > bound:
            return False, 0.0

        for sym in q1_syms | q2_syms:
            c1 = pdfa.counts.get((q1, sym), 0)
            c2 = pdfa.counts.get((q2, sym), 0)
            if abs((c1 / n1) - (c2 / n2)) > bound:
                return False, 0.0
            if c1 > 0 and c2 > 0:
                t1, t2 = pdfa.delta.get((q1, sym)), pdfa.delta.get((q2, sym))
                if t1 and t2 and t1 != t2 and not self._test_compatibility(pdfa, t1, t2, checked)[0]:
                    return False, 0.0

        return True, (n1 + n2) * (1.0 + len(common))


# Backwards-compat alias: old name for the ALERGIA engine.
NativeStateMergingLearner = NativeALERGIAEngine
