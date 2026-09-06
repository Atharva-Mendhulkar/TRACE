"""
Tests for Runtime Verification, Product Automaton, and Anomaly Classification (PRD §17, §19, §20, §38).
"""

from uuid import uuid4
import pytest

from trace.drift.detector import DriftDetector
from trace.models.pdfa import PDFA
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.policy.product import ProductAutomaton
from trace.schema.models import CESRecord, EventAttributes
from trace.verification.verifier import RuntimeVerifier


@pytest.fixture
def test_pdfa():
    pdfa = PDFA(q0="q0")
    # High probability: web_search (P=0.95), low probability: browse_rare (P=0.05)
    pdfa.add_transition("q0", "plan_step", "q1", frequency=100)
    pdfa.add_transition("q1", "web_search", "q2", frequency=95)
    pdfa.add_transition("q1", "browse_rare", "q2", frequency=5)
    pdfa.add_transition("q2", "terminate", "q_final", frequency=100)
    pdfa.mark_final("q_final")
    pdfa.recompute_all_probabilities()
    return pdfa


@pytest.fixture
def test_policy_dfa():
    source = """
    POLICY forbid_rare
      FORBID SEQUENCE [ plan_step, browse_rare ]
    """
    ast = PolicyParser.parse(source)
    return PolicyCompiler.compile(ast)


def test_product_automaton_classification(test_pdfa, test_policy_dfa):
    prod = ProductAutomaton(
        pdfa=test_pdfa,
        policy_dfa=test_policy_dfa,
        epsilon=0.10,  # 0.05 will trigger statistical anomaly
    )

    # 1. Normal transition: plan_step
    res1 = prod.step("plan_step")
    assert not res1.is_structural
    assert not res1.is_statistical
    assert not res1.is_policy
    assert not res1.is_violation

    # 2. Both statistical anomaly and policy violation on browse_rare!
    res2 = prod.step("browse_rare")
    assert not res2.is_structural
    assert res2.is_statistical, f"Expected statistical anomaly, P={res2.probability}"
    assert res2.is_policy, "Expected policy violation"
    assert "statistical" in res2.active_flags
    assert "policy" in res2.active_flags

    # 3. Structural anomaly on unknown action
    res3 = prod.step("unseen_tool_action")
    assert res3.is_structural
    assert "structural" in res3.active_flags


def test_runtime_verifier_and_explainability(test_pdfa):
    verifier = RuntimeVerifier(pdfa=test_pdfa, epsilon=0.10)
    trace_id = str(uuid4())

    ev1 = CESRecord(
        schema_version="1.0",
        event_id=str(uuid4()),
        trace_id=trace_id,
        span_id=trace_id,
        agent_id="test-agent",
        event_type="plan_step",
        symbol="plan_step",
        raw_symbol="planner_start",
        attributes=EventAttributes(param_schema_hash="hash", status="success"),
        timestamp="2026-09-07T00:00:00Z",
        framework="custom",
        framework_schema_version="1.0",
        adapter_version="1.0.0",
        sequence_no=0,
        status="success",
    )

    ev2 = CESRecord(
        schema_version="1.0",
        event_id=str(uuid4()),
        trace_id=trace_id,
        span_id=trace_id,
        agent_id="test-agent",
        event_type="tool_call",
        symbol="unauthorized_delete",
        raw_symbol="delete_db",
        attributes=EventAttributes(param_schema_hash="hash", status="success"),
        timestamp="2026-09-07T00:00:01Z",
        framework="custom",
        framework_schema_version="1.0",
        adapter_version="1.0.0",
        sequence_no=1,
        status="success",
    )

    resp1 = verifier.verify_event(ev1)
    assert not resp1.violation

    resp2 = verifier.verify_event(ev2)
    assert resp2.violation is not None
    expl = resp2.violation

    # PRD §21 contract assertions:
    assert expl.offending_action.canonical_symbol == "unauthorized_delete"
    assert expl.offending_action.framework_native_name == "delete_db"
    assert "structural" in expl.classification
    assert "web_search" in expl.expected_symbols_at_state
    assert expl.shortest_offending_suffix == ["plan_step", "unauthorized_delete"]


def test_drift_detector():
    detector = DriftDetector(agent_id="test-agent", window_size=30, min_sample_size=15)
    # Baseline normal NLL distribution: mean ~ 0.5
    baseline = [0.5 + 0.05 * (i % 5) for i in range(50)]
    detector.set_baseline(baseline)

    # Ingest shifted distribution: mean ~ 2.5
    drift_event = None
    for i in range(25):
        ev = detector.record_trace_conformance(2.5 + 0.1 * (i % 5))
        if ev:
            drift_event = ev
            break

    assert drift_event is not None
    assert drift_event.relearn_triggered
    assert drift_event.test_used == "ks_2samp+tail_quantile"
