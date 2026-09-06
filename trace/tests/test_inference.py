"""
Tests for Protocol Inference Engine & PDFA Formal Model (PRD §14, §15, §38).
"""

import pytest

from trace.inference.native_learner import NativeStateMergingLearner
from trace.inference.pta import PrefixTreeAcceptor
from trace.models.pdfa import PDFA
from trace.models.repository import ModelRepository


def test_pta_construction():
    traces = [
        ["plan_step", "web_search", "terminate"],
        ["plan_step", "file_read", "terminate"],
    ]
    pta = PrefixTreeAcceptor.build(traces)
    assert pta.q0 == "q0"
    assert "plan_step" in pta.get_outgoing_symbols("q0")
    # Tree should have distinct branches for web_search and file_read
    next_st, prob = pta.step("q0", "plan_step")
    assert prob == 1.0
    out_syms = pta.get_outgoing_symbols(next_st)
    assert "web_search" in out_syms
    assert "file_read" in out_syms


def test_native_learner_alergia_merge():
    # Repeated traces with stochastic branching
    traces = [
        ["plan_step", "web_search", "tool_result", "terminate"],
        ["plan_step", "web_search", "tool_result", "terminate"],
        ["plan_step", "file_read", "tool_result", "terminate"],
    ]
    learner = NativeStateMergingLearner(heuristic="alergia", alpha=0.1)
    pdfa = learner.fit(traces)

    assert len(pdfa.states) >= 3
    # Check probability normalization: sum P(q, sigma) <= 1 for all states
    for st in pdfa.states:
        total_p = sum(pdfa.P.get((st, sym), 0.0) for sym in pdfa.get_outgoing_symbols(st))
        assert total_p <= 1.0001, f"State {st} probability exceeds 1.0: {total_p}"


def test_pdfa_mean_nll_calculation():
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "plan_step", "q1", frequency=10)
    pdfa.add_transition("q1", "web_search", "q2", frequency=8)
    pdfa.add_transition("q1", "file_read", "q2", frequency=2)
    pdfa.mark_final("q2")
    pdfa.recompute_all_probabilities()

    # Normal trace
    mean_nll, nlls, has_struct = pdfa.compute_trace_mean_nll(["plan_step", "web_search"])
    assert not has_struct
    assert mean_nll > 0.0
    assert len(nlls) == 2

    # Structural anomaly
    mean_nll_bad, nlls_bad, has_struct_bad = pdfa.compute_trace_mean_nll(["plan_step", "unknown_action"])
    assert has_struct_bad


def test_model_repository_lifecycle():
    repo = ModelRepository(":memory:")
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "a", "q1", frequency=5)
    pdfa.mark_final("q1")
    pdfa.recompute_all_probabilities()

    model_id = repo.save_model(
        agent_id="test-agent",
        pdfa=pdfa,
        training_corpus_hash="hash123",
        learner_config={"heuristic": "alergia"},
    )

    # Validate
    val = repo.validate_model(model_id, training_alphabet=["a"])
    assert val["validation_passed"]

    # Candidate status
    m = repo.get_model(model_id)
    assert m["status"] == "CANDIDATE"

    # Promote to ACTIVE
    promoted = repo.promote_model(model_id)
    assert promoted
    active_m = repo.get_active_model("test-agent")
    assert active_m["model_id"] == model_id
    assert active_m["status"] == "ACTIVE"
