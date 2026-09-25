"""
Tests for Phase 2 Systems: TraceStore, and HITL Feedback Engine.
"""

from pathlib import Path
from uuid import uuid4

from trace.corpus.store import TraceStore
from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
from trace.models.repository import ModelLifecycleStatus, ModelRepository
from trace.models.pdfa import PDFA
from trace.schema.models import CESRecord, EventAttributes, ProvenanceInfo
from trace.verification.verifier import RuntimeVerifier


def _create_sample_event(
    trace_id: str,
    span_id: str,
    symbol: str,
    seq_no: int,
    agent_id: str = "agent-1",
    role: str = None,
    parent_span_id: str = None,
    depth: int = 0,
    event_type: str = "tool_call",
) -> CESRecord:
    return CESRecord(
        schema_version="1.0",
        event_id=str(uuid4()),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_id=agent_id,
        role=role,
        depth=depth,
        event_type=event_type,
        symbol=symbol,
        raw_symbol=symbol,
        attributes=EventAttributes(
            param_schema_hash="hash-123",
            status="success",
        ),
        timestamp="2026-09-07T12:00:00Z",
        framework="mcp",
        framework_schema_version="1.0",
        adapter_version="1.0.0",
        sequence_no=seq_no,
        status="success",
        provenance=ProvenanceInfo(
            timestamp_source="framework",
            ingested_at="2026-09-07T12:00:00Z",
        ),
    )


def test_trace_store_trace_and_role_corpus(tmp_path):
    store = TraceStore(str(tmp_path / "t.sqlite"))

    # Parent events
    t_id = str(uuid4())
    p_span = "span-parent"
    e1 = _create_sample_event(t_id, p_span, "plan", 0, agent_id="lead-agent")
    e2 = _create_sample_event(t_id, p_span, "delegate", 1, agent_id="lead-agent", event_type="delegate", role="reviewer")
    e3 = _create_sample_event(t_id, p_span, "terminate", 2, agent_id="lead-agent", event_type="terminate")

    # Child events
    c_span = "span-child"
    c1 = _create_sample_event(t_id, c_span, "review", 0, agent_id="lead-agent", role="reviewer", parent_span_id=p_span, depth=1)
    c2 = _create_sample_event(t_id, c_span, "return", 1, agent_id="lead-agent", role="reviewer", parent_span_id=p_span, depth=1, event_type="tool_result")

    store.write_events([e1, e2, e3, c1, c2])

    # Test trace retrieval
    trace = store.get_trace(t_id)
    assert len(trace) == 5

    # Test delegated trace by parent span id
    delegated = store.get_delegated_traces(p_span)
    assert len(delegated) == 2
    assert [d.symbol for d in delegated] == ["review", "return"]

    # Idempotent writes
    assert store.write_event(e1) is False


def test_model_repository_lifecycle(tmp_path):
    store = ModelRepository(str(tmp_path / "m.sqlite"))

    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "a", "q1", frequency=10)
    pdfa.mark_final("q1")

    # Save model
    model_id = store.save_model("test-agent", pdfa, training_corpus_hash="h", learner_config={})
    loaded = store.get_model(model_id)
    assert loaded is not None
    assert loaded["pdfa"].states == {"q0", "q1"}

    # Validation -> Candidate
    store.validate_model(model_id)

    # Promotion -> Promoted
    store.promote_model(model_id)

    # Activation -> Active
    store.activate_model(model_id)
    active = store.get_active_model("test-agent")
    assert active is not None
    assert active["pdfa"].states == {"q0", "q1"}


def test_incremental_verification():
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "start", "q1", frequency=10)
    pdfa.add_transition("q1", "process", "q2", frequency=10)
    pdfa.add_transition("q2", "terminate", "q2", frequency=10)
    pdfa.mark_final("q2")

    verifier = RuntimeVerifier(pdfa=pdfa)

    t_id = "trace-incremental"
    s_id = "span-incremental"

    ev1 = _create_sample_event(t_id, s_id, "start", 0)
    resp1 = verifier.verify_event(ev1)
    assert not resp1.violation
    assert resp1.allowed

    # Next event continues the same session
    ev2 = _create_sample_event(t_id, s_id, "process", 1)
    resp2 = verifier.verify_event(ev2)
    assert not resp2.violation
    assert resp2.allowed
    # Session advanced: learned state moved from q1 to q2
    assert verifier.sessions[(t_id, s_id)].q_learned == "q2"


def test_hitl_feedback_engine_lifecycle():
    store = RelationalFeedbackStore("sqlite:///:memory:")
    model_store = ModelRepository(":memory:")

    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "a", "q1", frequency=5)
    pdfa.mark_final("q1")
    model_id = model_store.save_model("agent-hitl", pdfa, training_corpus_hash="h", learner_config={})
    model_store.validate_model(model_id)

    engine = FeedbackEngine(store=store, model_repo=model_store)

    # 1. Record violation feedback
    v_id = str(uuid4())
    record = engine.record_feedback(
        violation_id=v_id,
        feedback_type="approve",
        reviewer="sec-engineer",
        comment="Legitimate maintenance workflow",
    )
    assert record.feedback_id is not None
    assert record.violation_id == v_id

    # 2. List feedback
    fb_list = engine.list_feedback()
    assert len(fb_list) == 1
    assert fb_list[0].reviewer == "sec-engineer"

    # 3. Candidate review and promotion
    rev_result = engine.review_candidate_model(
        model_id=model_id,
        action="approve",
        reviewer="sec-lead",
        comment="Model tested against benchmark",
    )
    assert rev_result["status"] == ModelLifecycleStatus.PROMOTED.value

    # 4. Activate promoted model
    act_result = engine.activate_promoted_model(model_id)
    assert act_result["status"] == ModelLifecycleStatus.ACTIVE.value

    # Verify model is active in repository
    active = model_store.get_active_model("agent-hitl")
    assert active is not None
    assert active["pdfa"].states == {"q0", "q1"}


def test_trace_store_outlier_quarantine():
    store = TraceStore(":memory:")

    # Baseline PDFA accepting: plan -> file_read -> terminate
    baseline_pdfa = PDFA(q0="q0")
    baseline_pdfa.add_transition("q0", "plan", "q1", frequency=10)
    baseline_pdfa.add_transition("q1", "file_read", "q2", frequency=10)
    baseline_pdfa.add_transition("q2", "terminate", "q_final", frequency=10)
    baseline_pdfa.mark_final("q_final")
    baseline_pdfa.recompute_all_probabilities()

    # 1. Normal trace matches baseline
    normal_tid = "trace-normal-001"
    normal_events = [
        _create_sample_event(normal_tid, "s1", "plan", 0, event_type="plan_step"),
        _create_sample_event(normal_tid, "s1", "file_read", 1, event_type="tool_call"),
        _create_sample_event(normal_tid, "s1", "terminate", 2, event_type="terminate"),
    ]
    inserted, is_quarantined = store.write_trace_safely(normal_events, baseline_pdfa=baseline_pdfa)
    assert inserted == 3
    assert not is_quarantined

    # 2. Poisoned/anomalous trace violates baseline transitions
    poison_tid = "trace-poison-002"
    poison_events = [
        _create_sample_event(poison_tid, "s2", "drop_db", 0, event_type="tool_call"),
        _create_sample_event(poison_tid, "s2", "exfiltrate", 1, event_type="tool_call"),
        _create_sample_event(poison_tid, "s2", "terminate", 2, event_type="terminate"),
    ]
    inserted_p, is_quarantined_p = store.write_trace_safely(poison_events, baseline_pdfa=baseline_pdfa)
    assert inserted_p == 3
    assert is_quarantined_p is True

    # 3. Quarantined trace is listed in quarantine registry
    quarantined = store.get_quarantined_traces()
    assert poison_tid in quarantined
    assert normal_tid not in quarantined

    # 4. Quarantined trace is excluded from training corpus
    corpus = store.get_corpus("agent-1")
    assert len(corpus) == 1
    assert corpus[0] == ["plan", "file_read", "terminate"]
