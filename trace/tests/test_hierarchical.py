"""
Tests for Hierarchical Delegation Folding & Multi-Agent Verification (PRD §16).
"""

import json
from pathlib import Path
import pytest

from trace.corpus.store import TraceStore
from trace.explainability.explainer import DelegationContext
from trace.inference.native_learner import NativeStateMergingLearner
from trace.ingestion.pipeline import IngestionPipeline
from trace.models.repository import ModelRepository
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.schema.models import CESRecord, EventAttributes
from trace.verification.verifier import HierarchicalRuntimeVerifier


@pytest.fixture
def hierarchical_db(tmp_path):
    db_file = tmp_path / "hier_test.sqlite"
    store = TraceStore(db_file)
    pipeline = IngestionPipeline(trace_store=store)

    # Ingest golden hierarchical traces
    fixture_path = Path(__file__).parent / "fixtures" / "hierarchical" / "hierarchical_traces.json"
    pipeline.ingest_file(str(fixture_path), framework="custom")

    # Add 5 more identical repetitions with different trace IDs to meet ALERGIA sample requirements
    for i in range(2, 7):
        t_id = f"trace-hier-00{i}"
        parent_span = f"span-p{i}"
        child_span = f"span-c{i}"
        events = [
            CESRecord(
                event_id=f"p00{i}-01", trace_id=t_id, span_id=parent_span,
                agent_id="primary-agent", role="primary", depth=0,
                event_type="plan_step", symbol="plan", raw_symbol="plan_step",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:00Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=1,
            ),
            CESRecord(
                event_id=f"p00{i}-02", trace_id=t_id, span_id=parent_span,
                agent_id="primary-agent", role="primary", depth=0,
                event_type="tool_call", symbol="web_search", raw_symbol="web_search",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:01Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=2,
            ),
            CESRecord(
                event_id=f"p00{i}-03", trace_id=t_id, span_id=parent_span,
                agent_id="primary-agent", role="primary", depth=0,
                event_type="delegate", symbol="delegate(reviewer)", raw_symbol="delegate(reviewer)",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:02Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=3,
            ),
            # Child trace
            CESRecord(
                event_id=f"c00{i}-01", trace_id=t_id, span_id=child_span, parent_span_id=parent_span,
                agent_id=f"reviewer-agent-0{i}", role="reviewer", depth=1,
                event_type="tool_call", symbol="receive", raw_symbol="receive",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:03Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=4,
            ),
            CESRecord(
                event_id=f"c00{i}-02", trace_id=t_id, span_id=child_span, parent_span_id=parent_span,
                agent_id=f"reviewer-agent-0{i}", role="reviewer", depth=1,
                event_type="tool_call", symbol="inspect", raw_symbol="inspect",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:04Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=5,
            ),
            CESRecord(
                event_id=f"c00{i}-03", trace_id=t_id, span_id=child_span, parent_span_id=parent_span,
                agent_id=f"reviewer-agent-0{i}", role="reviewer", depth=1,
                event_type="tool_call", symbol="tool_call", raw_symbol="tool_call",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:05Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=6,
            ),
            CESRecord(
                event_id=f"c00{i}-04", trace_id=t_id, span_id=child_span, parent_span_id=parent_span,
                agent_id=f"reviewer-agent-0{i}", role="reviewer", depth=1,
                event_type="terminate", symbol="return", raw_symbol="return",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:06Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=7,
            ),
            # Parent resume
            CESRecord(
                event_id=f"p00{i}-04", trace_id=t_id, span_id=parent_span,
                agent_id="primary-agent", role="primary", depth=0,
                event_type="tool_call", symbol="merge", raw_symbol="merge",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:07Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=8,
            ),
            CESRecord(
                event_id=f"p00{i}-05", trace_id=t_id, span_id=parent_span,
                agent_id="primary-agent", role="primary", depth=0,
                event_type="terminate", symbol="terminate", raw_symbol="terminate",
                attributes=EventAttributes(param_schema_hash="h1", status="success"),
                timestamp=f"2026-09-07T10:{i:02d}:08Z", framework="custom",
                framework_schema_version="1.0", adapter_version="1.0", sequence_no=9,
            ),
        ]
        store.write_events(events)

    return db_file


def test_corpus_separation(hierarchical_db):
    store = TraceStore(hierarchical_db)

    # 1. Parent corpus: should have 6 traces of length 5 (no inlining of child events)
    parent_corpus = store.get_corpus("primary-agent")
    assert len(parent_corpus) == 6
    for trace in parent_corpus:
        assert trace == ["plan", "web_search", "delegate(reviewer)", "merge", "terminate"]

    # 2. Reviewer role corpus: should have 6 traces of length 4
    reviewer_corpus = store.get_role_corpus("reviewer")
    assert len(reviewer_corpus) == 6
    for trace in reviewer_corpus:
        assert trace == ["receive", "inspect", "tool_call", "return"]


def test_role_model_training_and_resolution(hierarchical_db):
    store = TraceStore(hierarchical_db)
    repo = ModelRepository(hierarchical_db)

    # Train parent PDFA
    parent_corpus = store.get_corpus("primary-agent")
    parent_learner = NativeStateMergingLearner(heuristic="alergia", alpha=0.05)
    parent_pdfa = parent_learner.fit(parent_corpus)

    parent_model_id = repo.save_model(
        agent_id="primary-agent",
        pdfa=parent_pdfa,
        training_corpus_hash="p_hash",
        learner_config={"engine": "native-alergia"},
    )
    repo.validate_model(parent_model_id)
    repo.promote_model(parent_model_id)

    # Train reviewer role PDFA
    reviewer_corpus = store.get_role_corpus("reviewer")
    reviewer_learner = NativeStateMergingLearner(heuristic="alergia", alpha=0.05)
    reviewer_pdfa = reviewer_learner.fit(reviewer_corpus)

    reviewer_model_id = repo.save_model(
        agent_id="role:reviewer",
        role="reviewer",
        pdfa=reviewer_pdfa,
        training_corpus_hash="r_hash",
        learner_config={"engine": "native-alergia", "role": "reviewer"},
    )
    repo.validate_model(reviewer_model_id)
    repo.promote_model(reviewer_model_id)

    # Check resolution
    active_parent = repo.get_active_model("primary-agent")
    assert active_parent is not None
    assert active_parent["model_id"] == parent_model_id

    active_reviewer = repo.get_active_model_for_role("reviewer")
    assert active_reviewer is not None
    assert active_reviewer["model_id"] == reviewer_model_id
    assert active_reviewer["role"] == "reviewer"


def test_hierarchical_normal_verification(hierarchical_db):
    store = TraceStore(hierarchical_db)
    repo = ModelRepository(hierarchical_db)

    parent_corpus = store.get_corpus("primary-agent")
    parent_pdfa = NativeStateMergingLearner().fit(parent_corpus)

    reviewer_corpus = store.get_role_corpus("reviewer")
    reviewer_pdfa = NativeStateMergingLearner().fit(reviewer_corpus)

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=parent_pdfa,
        role_pdfas={"reviewer": reviewer_pdfa},
        mode="gate",
    )

    # Replay trace-hier-001
    events = store.get_trace("trace-hier-001")
    responses = verifier.replay_trace(events)

    assert len(responses) == 9
    for r in responses:
        assert r.allowed is True
        assert r.violation is None


def test_hierarchical_child_structural_violation(hierarchical_db):
    store = TraceStore(hierarchical_db)
    parent_pdfa = NativeStateMergingLearner().fit(store.get_corpus("primary-agent"))
    reviewer_pdfa = NativeStateMergingLearner().fit(store.get_role_corpus("reviewer"))

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=parent_pdfa,
        role_pdfas={"reviewer": reviewer_pdfa},
        mode="gate",
    )

    # Create trace with unauthorized child action
    events = [
        CESRecord(
            event_id="e1", trace_id="trace-viol", span_id="p1", agent_id="primary-agent",
            role="primary", depth=0, event_type="plan_step", symbol="plan", raw_symbol="plan",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:00Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=1,
        ),
        CESRecord(
            event_id="e2", trace_id="trace-viol", span_id="p1", agent_id="primary-agent",
            role="primary", depth=0, event_type="delegate", symbol="delegate(reviewer)", raw_symbol="delegate",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:01Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=2,
        ),
        # Child starts
        CESRecord(
            event_id="e3", trace_id="trace-viol", span_id="c1", parent_span_id="p1", agent_id="sub",
            role="reviewer", depth=1, event_type="tool_call", symbol="receive", raw_symbol="receive",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:02Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=3,
        ),
        # Child unauthorized action: delete_record
        CESRecord(
            event_id="e4", trace_id="trace-viol", span_id="c1", parent_span_id="p1", agent_id="sub",
            role="reviewer", depth=1, event_type="tool_call", symbol="delete_record", raw_symbol="delete_record",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:03Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=4,
        ),
    ]

    responses = verifier.replay_trace(events)
    r4 = responses[3]
    assert r4.allowed is False
    assert r4.violation is not None
    assert "structural" in r4.classification

    # PRD §21.1: verify delegation context in violation
    ctx = r4.violation.delegation_context
    assert ctx.depth == 1
    assert ctx.role == "reviewer"
    assert ctx.parent_span_id == "p1"
    assert "Delegation depth=1" in r4.violation.render_human_readable()


def test_hierarchical_max_depth_exceeded(hierarchical_db):
    store = TraceStore(hierarchical_db)
    parent_pdfa = NativeStateMergingLearner().fit(store.get_corpus("primary-agent"))

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=parent_pdfa,
        max_delegation_depth=3,
        mode="gate",
    )

    deep_event = CESRecord(
        event_id="e-deep", trace_id="trace-deep", span_id="c9", parent_span_id="p1",
        agent_id="sub", role="deep_agent", depth=4, event_type="tool_call",
        symbol="compute", raw_symbol="compute",
        attributes=EventAttributes(param_schema_hash="h", status="success"),
        timestamp="2026-09-07T12:00:00Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=1,
    )

    resp = verifier.verify_event(deep_event)
    assert resp.allowed is False
    assert "delegation_depth_exceeded" in resp.classification


def test_hierarchical_orphan_child_span(hierarchical_db):
    store = TraceStore(hierarchical_db)
    parent_pdfa = NativeStateMergingLearner().fit(store.get_corpus("primary-agent"))

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=parent_pdfa,
        mode="gate",
    )

    orphan_event = CESRecord(
        event_id="e-orphan", trace_id="trace-orphan", span_id="c_orphan",
        parent_span_id="non_existent_span_id_12345",
        agent_id="sub", role="reviewer", depth=1, event_type="tool_call",
        symbol="inspect", raw_symbol="inspect",
        attributes=EventAttributes(param_schema_hash="h", status="success"),
        timestamp="2026-09-07T12:00:00Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=1,
    )

    resp = verifier.verify_event(orphan_event)
    assert resp.allowed is False
    assert "orphan_child_span" in resp.classification


def test_hierarchical_missing_child_trace(hierarchical_db):
    store = TraceStore(hierarchical_db)
    parent_pdfa = NativeStateMergingLearner().fit(store.get_corpus("primary-agent"))

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=parent_pdfa,
        mode="observe",
    )

    # Parent initiates delegation but terminates without child events
    events = [
        CESRecord(
            event_id="e1", trace_id="trace-miss", span_id="p1", agent_id="primary-agent",
            role="primary", depth=0, event_type="delegate", symbol="delegate(reviewer)", raw_symbol="delegate",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:00Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=1,
        ),
        CESRecord(
            event_id="e2", trace_id="trace-miss", span_id="p1", agent_id="primary-agent",
            role="primary", depth=0, event_type="terminate", symbol="terminate", raw_symbol="terminate",
            attributes=EventAttributes(param_schema_hash="h", status="success"),
            timestamp="2026-09-07T12:00:01Z", framework="custom", framework_schema_version="1.0", adapter_version="1.0", sequence_no=2,
        ),
    ]

    responses = verifier.replay_trace(events)
    assert len(responses) == 2
    assert "missing_child_trace" in responses[1].classification
