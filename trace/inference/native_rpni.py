"""
Native RPNI (Regular Positive and Negative Inference) Engine (PRD §14.3).
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Set, Tuple

from trace.inference.base import LearnerEngine
from trace.inference.pta import PrefixTreeAcceptor
from trace.models.pdfa import PDFA


class NativeRPNIEngine(LearnerEngine):
    """
    Reference/baseline passive automata learner implementing RPNI
    (Regular Positive and Negative Inference) state-merging (Oncina & García 1992).
    """

    name = "native-rpni"

    def __init__(self, negative_traces: Optional[List[List[str]]] = None):
        self.negative_traces = negative_traces or []

    def fit(self, traces: List[List[str]]) -> PDFA:
        if not traces:
            return PDFA(q0="q0")

        pta = PrefixTreeAcceptor.build(traces)
        red_states: Set[str] = {pta.q0}
        blue_states: Set[str] = {
            tgt for (s, _), tgt in pta.delta.items() if s == pta.q0 and tgt not in red_states
        }

        pdfa = copy.deepcopy(pta)

        while blue_states:
            q_blue = sorted(list(blue_states))[0]
            merged = False

            for q_red in sorted(list(red_states)):
                candidate = self._merge_and_determinize(pdfa, q_red, q_blue)
                if candidate is not None and self._is_consistent(candidate):
                    pdfa = candidate
                    merged = True
                    break

            if not merged:
                red_states.add(q_blue)

            # Update blue states: successors of red that are not red
            blue_states = set()
            for q_r in red_states:
                for (s, _), tgt in pdfa.delta.items():
                    if s == q_r and tgt not in red_states:
                        blue_states.add(tgt)

        pdfa.recompute_all_probabilities()
        return pdfa

    def _merge_and_determinize(self, pdfa: PDFA, q_red: str, q_blue: str) -> Optional[PDFA]:
        cand = copy.deepcopy(pdfa)
        queue = [(q_red, q_blue)]

        while queue:
            k_st, m_st = queue.pop(0)
            if k_st == m_st:
                continue

            cand.final_counts[k_st] = cand.final_counts.get(k_st, 0) + cand.final_counts.get(m_st, 0)
            if m_st in cand.F:
                cand.F.add(k_st)

            for (from_s, sym), target in list(cand.delta.items()):
                if target == m_st:
                    cand.delta[(from_s, sym)] = k_st

            for (from_s, sym), m_target in list(cand.delta.items()):
                if from_s == m_st:
                    m_count = cand.counts.get((m_st, sym), 0)
                    if (k_st, sym) in cand.delta:
                        k_target = cand.delta[(k_st, sym)]
                        cand.counts[(k_st, sym)] += m_count
                        if k_target != m_target:
                            queue.append((k_target, m_target))
                    else:
                        cand.delta[(k_st, sym)] = m_target
                        cand.counts[(k_st, sym)] = m_count

                    del cand.delta[(m_st, sym)]
                    if (m_st, sym) in cand.counts:
                        del cand.counts[(m_st, sym)]

            cand.states.discard(m_st)
            cand.F.discard(m_st)

        return cand

    def _is_consistent(self, candidate: PDFA) -> bool:
        """Check if candidate automaton rejects all negative samples."""
        for neg_trace in self.negative_traces:
            st = candidate.q0
            rejected = False
            for sym in neg_trace:
                if (st, sym) not in candidate.delta:
                    rejected = True
                    break
                st = candidate.delta[(st, sym)]
            if not rejected and st in candidate.F:
                # Accepted a negative trace! Inconsistent.
                return False
        return True
