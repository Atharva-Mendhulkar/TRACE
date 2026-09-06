"""
Prefix Tree Acceptor (PTA) Construction (PRD §14.2).
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple
from trace.models.pdfa import PDFA


class PrefixTreeAcceptor:
    """Constructs a Prefix Tree Acceptor (PTA) from positive trace sequences."""

    @staticmethod
    def build(traces: List[List[str]]) -> PDFA:
        """
        Build a tree-shaped PDFA where common prefixes share states.
        Frequency counts are tracked at every transition and termination.
        """
        pdfa = PDFA(q0="q0")
        state_counter = 0

        # Mapping: prefix tuple of symbols -> state name
        prefix_to_state: Dict[Tuple[str, ...], str] = {(): "q0"}

        for trace in traces:
            current_prefix: Tuple[str, ...] = ()
            for sym in trace:
                next_prefix = current_prefix + (sym,)
                if next_prefix not in prefix_to_state:
                    state_counter += 1
                    new_state = f"q{state_counter}"
                    prefix_to_state[next_prefix] = new_state
                    pdfa.add_state(new_state)

                from_st = prefix_to_state[current_prefix]
                to_st = prefix_to_state[next_prefix]
                pdfa.add_transition(from_st, sym, to_st, frequency=1, recompute_probs=False)
                current_prefix = next_prefix

            # End of trace mark final
            final_st = prefix_to_state[current_prefix]
            pdfa.mark_final(final_st, count=1)

        pdfa.recompute_all_probabilities()
        return pdfa
