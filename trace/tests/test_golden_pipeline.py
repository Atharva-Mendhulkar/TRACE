"""
Golden Deterministic Fixtures & End-to-End Replay Contract Test (PRD Phase 1 Acceptance Criteria).
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from trace.inference.native_alergia import NativeALERGIAEngine
from trace.inference.native_edsm import NativeEDSMEngine
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.schema.models import CESRecord, EventAttributes
from trace.verification.verifier import RuntimeVerifier

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"


def test_golden_deterministic_pipeline():
    # 1. Load golden trace
    trace_file = GOLDEN_DIR / "traces" / "golden_agent_trace.json"
    with open(trace_file, "r", encoding="utf-8") as f:
        raw_events = json.load(f)
    records = [CESRecord(**r) for r in raw_events]
    trace_symbols = [r.symbol for r in records]

    # 2. Learn automaton via NativeALERGIAEngine
    alergia_engine = NativeALERGIAEngine(alpha=0.05)
    learned_pdfa = alergia_engine.fit([trace_symbols])
    learned_dict = learned_pdfa.to_dict()

    # 3. Assert match against golden automaton fixture
    automaton_file = GOLDEN_DIR / "automata" / "golden_alergia_automaton.json"
    with open(automaton_file, "r", encoding="utf-8") as f:
        expected_automaton = json.load(f)

    assert learned_dict["q0"] == expected_automaton["q0"]
    assert sorted(learned_dict["states"]) == sorted(expected_automaton["states"])
    assert sorted(learned_dict["alphabet"]) == sorted(expected_automaton["alphabet"])
    assert sorted(learned_dict["final_states"]) == sorted(expected_automaton["final_states"])
    assert len(learned_dict["transitions"]) == len(expected_automaton["transitions"])

    # 4. Verify EDSM engine produces deterministic automaton as well
    edsm_engine = NativeEDSMEngine()
    edsm_pdfa = edsm_engine.fit([trace_symbols])
    assert len(edsm_pdfa.states) == len(learned_pdfa.states)

    # 5. Load golden policy
    policy_file = GOLDEN_DIR / "policies" / "golden_safety.policy"
    policy_src = policy_file.read_text(encoding="utf-8")
    policy_ast = PolicyParser.parse(policy_src)
    policy_dfa = PolicyCompiler.compile(policy_ast, alphabet=learned_pdfa.alphabet)

    # 6. Replay golden trace through RuntimeVerifier
    verifier = RuntimeVerifier(pdfa=learned_pdfa, policy_dfa=policy_dfa, mode="observe")
    responses = verifier.replay_trace(records)

    # 7. Compare against golden expected results
    expected_file = GOLDEN_DIR / "expected" / "golden_verification_results.json"
    with open(expected_file, "r", encoding="utf-8") as f:
        expected_results = json.load(f)

    assert len(responses) == len(expected_results)
    for resp, exp in zip(responses, expected_results):
        assert resp.event_id == exp["event_id"]
        assert resp.classification == exp["classification"]
        assert resp.allowed == exp["allowed"]
        assert resp.running_mean_nll == pytest.approx(exp["running_mean_nll"])
        assert (resp.violation is not None) == exp["is_violation"]

    # 8. Injected deviation test: inject unconfirmed delete_record
    deviant_event = CESRecord(
        schema_version="1.0",
        event_id=str(uuid4()),
        trace_id=records[0].trace_id,
        span_id=records[0].span_id,
        agent_id="golden-agent",
        event_type="tool_call",
        symbol="delete_database",
        raw_symbol="delete_database",
        attributes=EventAttributes(
            param_schema_hash="hash",
            status="success",
        ),
        timestamp="2026-09-07T00:00:10Z",
        framework="custom",
        framework_schema_version="1.0",
        adapter_version="1.0.0",
        sequence_no=5,
        status="success",
    )

    resp_deviant = verifier.verify_event(deviant_event)
    assert resp_deviant.violation is not None
    assert "structural" in resp_deviant.classification
    assert "policy" in resp_deviant.classification
    assert resp_deviant.violation.offending_action.canonical_symbol == "delete_database"
    assert resp_deviant.violation.policy_rule_if_applicable == "golden_safety_contract"
