"""
Native ALERGIA / MDI Probabilistic State-Merging Engine (PRD §14.3).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Set, Tuple

from trace.inference.base import LearnerEngine
from trace.inference.pta import PrefixTreeAcceptor
from trace.models.pdfa import PDFA


class NativeALERGIAEngine(LearnerEngine):
    """
    Reference/baseline passive automata learner implementing pure ALERGIA / MDI
    probabilistic state-merging with Hoeffding-bound statistical compatibility testing.
    """

    name = "native-alergia"

    def __init__(self, alpha: float = 0.05, min_support: int = 1):
        self.alpha = alpha
        self.min_support = min_support

    def fit(self, traces: List[List[str]]) -> PDFA:
        if not traces:
            return PDFA(q0="q0")

        # 1. Build Prefix Tree Acceptor
        pta = PrefixTreeAcceptor.build(traces)

        # 2. State-merging loop
        merged_pdfa = self._merge_states(pta)
        merged_pdfa.recompute_all_probabilities()

        # 3. Canonical relabeling of states
        return self._canonicalize_states(merged_pdfa)

    def _merge_states(self, initial_pdfa: PDFA) -> PDFA:
        pdfa = copy.deepcopy(initial_pdfa)

        while True:
            best_pair: Optional[Tuple[str, str]] = None
            best_score = -1.0

            states = sorted(
                list(pdfa.states),
                key=lambda s: (pdfa.state_totals.get(s, 0) + pdfa.final_counts.get(s, 0)),
                reverse=True,
            )

            for i in range(len(states)):
                q1 = states[i]
                for j in range(i + 1, len(states)):
                    q2 = states[j]
                    if q1 not in pdfa.states or q2 not in pdfa.states:
                        continue

                    is_compat, score = self._test_compatibility(pdfa, q1, q2)
                    if is_compat and score > best_score:
                        best_score = score
                        best_pair = (q1, q2)

            if best_pair is None:
                break

            q_keep, q_merge = best_pair
            pdfa = self._execute_merge(pdfa, q_keep, q_merge)

        return pdfa

    def _test_compatibility(
        self, pdfa: PDFA, q1: str, q2: str, checked: Optional[Set[Tuple[str, str]]] = None
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

        q1_syms = pdfa.get_outgoing_symbols(q1)
        q2_syms = pdfa.get_outgoing_symbols(q2)
        common_symbols = q1_syms.intersection(q2_syms)

        q1_is_final = pdfa.final_counts.get(q1, 0) > 0
        q2_is_final = pdfa.final_counts.get(q2, 0) > 0

        if not common_symbols and not (q1_is_final and q2_is_final):
            return False, 0.0

        if q1_is_final != q2_is_final:
            if n1 > 1 and n2 > 1:
                return False, 0.0

        eps1 = math.sqrt((1.0 / (2.0 * n1)) * math.log(2.0 / self.alpha))
        eps2 = math.sqrt((1.0 / (2.0 * n2)) * math.log(2.0 / self.alpha))
        bound = min(0.8, eps1 + eps2)

        # 1. Compare termination probabilities
        term_diff = abs((pdfa.final_counts.get(q1, 0) / n1) - (pdfa.final_counts.get(q2, 0) / n2))
        if term_diff > bound:
            return False, 0.0

        # 2. Compare transition probabilities for all symbols
        all_symbols = q1_syms.union(q2_syms)

        for sym in all_symbols:
            c1 = pdfa.counts.get((q1, sym), 0)
            c2 = pdfa.counts.get((q2, sym), 0)
            diff = abs((c1 / n1) - (c2 / n2))
            if diff > bound:
                return False, 0.0

            if c1 > 0 and c2 > 0:
                t1 = pdfa.delta.get((q1, sym))
                t2 = pdfa.delta.get((q2, sym))
                if t1 and t2 and t1 != t2:
                    sub_compat, _ = self._test_compatibility(pdfa, t1, t2, checked)
                    if not sub_compat:
                        return False, 0.0

        evidence_score = (n1 + n2) * (1.0 + len(common_symbols))
        return True, evidence_score

    def _execute_merge(self, pdfa: PDFA, q_keep: str, q_merge: str) -> PDFA:
        if q_keep == q_merge:
            return pdfa

        queue: List[Tuple[str, str]] = [(q_keep, q_merge)]

        while queue:
            k_st, m_st = queue.pop(0)
            if k_st == m_st:
                continue

            pdfa.final_counts[k_st] = pdfa.final_counts.get(k_st, 0) + pdfa.final_counts.get(m_st, 0)
            if m_st in pdfa.F:
                pdfa.F.add(k_st)

            for (from_s, sym), target in list(pdfa.delta.items()):
                if target == m_st:
                    pdfa.delta[(from_s, sym)] = k_st

            for (from_s, sym), m_target in list(pdfa.delta.items()):
                if from_s == m_st:
                    m_count = pdfa.counts.get((m_st, sym), 0)
                    if (k_st, sym) in pdfa.delta:
                        k_target = pdfa.delta[(k_st, sym)]
                        pdfa.counts[(k_st, sym)] += m_count
                        if k_target != m_target:
                            queue.append((k_target, m_target))
                    else:
                        pdfa.delta[(k_st, sym)] = m_target
                        pdfa.counts[(k_st, sym)] = m_count

                    del pdfa.delta[(m_st, sym)]
                    if (m_st, sym) in pdfa.counts:
                        del pdfa.counts[(m_st, sym)]

            pdfa.states.discard(m_st)
            pdfa.F.discard(m_st)
            pdfa.state_totals.pop(m_st, None)
            pdfa.final_counts.pop(m_st, None)

        for st in pdfa.states:
            pdfa.state_totals[st] = sum(
                cnt for (s, _), cnt in pdfa.counts.items() if s == st
            )

        return pdfa

    def _canonicalize_states(self, pdfa: PDFA) -> PDFA:
        new_pdfa = PDFA(q0="q0", alphabet=pdfa.alphabet)
        state_map: Dict[str, str] = {pdfa.q0: "q0"}
        counter = 0

        queue = [pdfa.q0]
        visited = {pdfa.q0}

        while queue:
            st = queue.pop(0)
            curr_alias = state_map[st]
            new_pdfa.add_state(curr_alias, is_final=(st in pdfa.F))
            new_pdfa.final_counts[curr_alias] = pdfa.final_counts.get(st, 0)

            outgoing = sorted(
                [(sym, tgt) for (s, sym), tgt in pdfa.delta.items() if s == st],
                key=lambda x: x[0],
            )

            for sym, tgt in outgoing:
                if tgt not in state_map:
                    counter += 1
                    state_map[tgt] = f"q{counter}"
                if tgt not in visited:
                    visited.add(tgt)
                    queue.append(tgt)

                tgt_alias = state_map[tgt]
                freq = pdfa.counts.get((st, sym), 1)
                new_pdfa.add_transition(curr_alias, sym, tgt_alias, frequency=freq, recompute_probs=False)

        new_pdfa.recompute_all_probabilities()
        return new_pdfa
