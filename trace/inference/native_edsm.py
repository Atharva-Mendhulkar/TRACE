"""
Native EDSM (Evidence-Driven State Merging) Engine (PRD §14.3).
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Set, Tuple

from trace.inference.base import LearnerEngine
from trace.inference.pta import PrefixTreeAcceptor
from trace.models.pdfa import PDFA


class NativeEDSMEngine(LearnerEngine):
    """
    Reference/baseline passive automata learner implementing pure EDSM
    (Evidence-Driven State Merging) greedy merge search based on transition agreement.
    """

    name = "native-edsm"

    def __init__(self, min_evidence: int = 1):
        self.min_evidence = min_evidence

    def fit(self, traces: List[List[str]]) -> PDFA:
        if not traces:
            return PDFA(q0="q0")

        # 1. Build Prefix Tree Acceptor
        pta = PrefixTreeAcceptor.build(traces)

        # 2. Greedy evidence-driven merge loop
        merged_pdfa = self._merge_states(pta)
        merged_pdfa.recompute_all_probabilities()

        # 3. Canonical relabeling of states
        return self._canonicalize_states(merged_pdfa)

    def _merge_states(self, initial_pdfa: PDFA) -> PDFA:
        pdfa = copy.deepcopy(initial_pdfa)

        while True:
            best_pair: Optional[Tuple[str, str]] = None
            max_evidence = -1

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

                    # Evaluate EDSM evidence
                    is_valid, evidence = self._evaluate_evidence(pdfa, q1, q2)
                    if is_valid and evidence > max_evidence and evidence >= self.min_evidence:
                        max_evidence = evidence
                        best_pair = (q1, q2)

            if best_pair is None:
                break

            q_keep, q_merge = best_pair
            pdfa = self._execute_merge(pdfa, q_keep, q_merge)

        return pdfa

    def _evaluate_evidence(
        self, pdfa: PDFA, q1: str, q2: str, checked: Optional[Set[Tuple[str, str]]] = None
    ) -> Tuple[bool, int]:
        """Compute matching transition evidence score for EDSM."""
        if checked is None:
            checked = set()

        pair = (min(q1, q2), max(q1, q2))
        if pair in checked:
            return True, 0
        checked.add(pair)

        q1_syms = pdfa.get_outgoing_symbols(q1)
        q2_syms = pdfa.get_outgoing_symbols(q2)
        common = q1_syms.intersection(q2_syms)

        q1_is_final = pdfa.final_counts.get(q1, 0) > 0
        q2_is_final = pdfa.final_counts.get(q2, 0) > 0

        # Disjoint non-final states without common transitions have zero evidence
        if not common and not (q1_is_final and q2_is_final):
            return False, 0

        evidence = len(common)
        if q1_is_final and q2_is_final:
            evidence += 1

        # Recursively test target consistency
        for sym in common:
            t1 = pdfa.delta.get((q1, sym))
            t2 = pdfa.delta.get((q2, sym))
            if t1 and t2 and t1 != t2:
                valid, sub_ev = self._evaluate_evidence(pdfa, t1, t2, checked)
                if not valid:
                    return False, 0
                evidence += sub_ev

        return True, evidence

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
