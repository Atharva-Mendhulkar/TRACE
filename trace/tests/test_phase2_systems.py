"""
Tests for Phase 2 Systems: PostgresStore, Redis Session Cache, and HITL Feedback Engine.
"""

from pathlib import Path
import pytest
from uuid import uuid4

from trace.models.pdfa import PDFA
from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
from trace.models.repository import ModelLifecycleStatus
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.schema.models import (
    CESRecord,
    EventAttributes,
    ErrorInfo,
    ProvenanceInfo,
    ViolationRecord,
)
from trace.storage.postgres import PostgresStore
from trace.verification.session_cache import (
    InMemorySessionCache,
    RedisSessionCache,
    VerificationSessionState,
)
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


def test_postgres_store_trace_and_role_corpus():
    store = PostgresStore("sqlite:///:memory:")

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

    store.insert_events_batch([e1, e2, e3, c1, c2])

    # Test trace retrieval
    trace = store.get_trace(t_id)
    assert len(trace) == 5

    # Test parent corpus (depth 0, opaque delegate(role))
    parent_corpus = store.get_corpus("lead-agent")
    assert len(parent_corpus) == 1
    assert parent_corpus[0] == ["plan", "delegate(reviewer)", "terminate"]

    # Test child role corpus
    role_corpus = store.get_role_corpus("reviewer")
    assert len(role_corpus) == 1
    assert role_corpus[0] == ["review", "return"]

    # Test delegated trace by parent span id
    delegated = store.get_delegated_traces(p_span)
    assert len(delegated) == 2
    assert [d.symbol for d in delegated] == ["review", "return"]


def test_postgres_store_model_lifecycle():
    store = PostgresStore("sqlite:///:memory:")

    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "a", "q1", frequency=10)
    pdfa.mark_final("q1")

    # Save model
    model_id = store.save_model("test-agent", pdfa, role=None)
    loaded = store.get_model(model_id)
    assert loaded is not None
    assert loaded.states == {"q0", "q1"}
    assert loaded.alphabet == {"a"}

    # Validation -> Candidate
    store.validate_model(model_id)

    # Promotion -> Promoted
    store.promote_model(model_id)

    # Activation -> Active
    store.activate_model(model_id)
    active = store.get_active_model("test-agent")
    assert active is not None
    assert active.states == {"q0", "q1"}


def test_postgres_store_vector_similarity():
    store = PostgresStore("sqlite:///:memory:")

    store.save_centroid(1, "search_web", [1.0, 0.0, 0.0])
    store.save_centroid(1, "delete_file", [0.0, 1.0, 0.0])

    # Exact match query
    res = store.find_nearest_centroid(1, [0.99, 0.01, 0.0], threshold=0.1)
    assert res is not None
    sym, dist = res
    assert sym == "search_web"
    assert dist < 0.05

    # Orthogonal query exceeding threshold
    res_far = store.find_nearest_centroid(1, [0.0, 0.0, 1.0], threshold=0.35)
    assert res_far is None


def test_session_cache_implementations():
    in_mem = InMemorySessionCache()
    redis_cache = RedisSessionCache("redis://127.0.0.1:6379/15")

    for cache in [in_mem, redis_cache]:
        state = VerificationSessionState(
            trace_id="t1",
            span_id="s1",
            model_id="m1",
            policy_id="p1",
            current_learned_state="q2",
            current_policy_state="p1",
            running_mean_nll=0.45,
            running_events_count=3,
            last_event_time="2026-09-07T12:00:00Z",
        )
        cache.set_session(state)
        retrieved = cache.get_session("t1", "s1")
        assert retrieved is not None
        assert retrieved.trace_id == "t1"
        assert retrieved.current_learned_state == "q2"
        assert retrieved.running_mean_nll == 0.45

        cache.delete_session("t1", "s1")
        assert cache.get_session("t1", "s1") is None


def test_incremental_verification_with_session_cache():
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "start", "q1", frequency=10)
    pdfa.add_transition("q1", "process", "q2", frequency=10)
    pdfa.add_transition("q2", "terminate", "q2", frequency=10)
    pdfa.mark_final("q2")

    cache = InMemorySessionCache()
    verifier = RuntimeVerifier(pdfa=pdfa)

    t_id = "trace-incremental"
    s_id = "span-incremental"

    ev1 = _create_sample_event(t_id, s_id, "start", 0)
    resp1 = verifier.verify_event(ev1, session_cache=cache)
    assert not resp1.violation
    assert resp1.allowed

    # Verify session cache updated
    cached1 = cache.get_session(t_id, s_id)
    assert cached1 is not None
    assert cached1.current_learned_state == "q1"
    assert cached1.running_events_count == 1

    # Next event uses cached state
    ev2 = _create_sample_event(t_id, s_id, "process", 1)
    resp2 = verifier.verify_event(ev2, session_cache=cache)
    assert not resp2.violation
    assert resp2.allowed

    cached2 = cache.get_session(t_id, s_id)
    assert cached2 is not None
    assert cached2.current_learned_state == "q2"
    assert cached2.running_events_count == 2


def test_hitl_feedback_engine_lifecycle():
    store = RelationalFeedbackStore("sqlite:///:memory:")
    model_store = PostgresStore("sqlite:///:memory:")

    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "a", "q1", frequency=5)
    pdfa.mark_final("q1")
    model_id = model_store.save_model("agent-hitl", pdfa)
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
    assert active.states == {"q0", "q1"}
