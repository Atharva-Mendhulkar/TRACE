"""
Unit and Integration Tests for Phase 3 Production Systems (API, Streaming, and Benchmarks).
"""

import asyncio
from pathlib import Path
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from trace.api.server import create_app
from trace.benchmarks.evaluator import BenchmarkSuite
from trace.models.pdfa import PDFA
from trace.models.repository import ModelRepository
from trace.schema.models import CESRecord, EventAttributes, ProvenanceInfo
from trace.streaming.consumer import AsyncQueueConsumer, StreamingVerificationWorker
from trace.verification.verifier import RuntimeVerifier


def _create_sample_record(
    trace_id: str, span_id: str, symbol: str, seq_no: int, agent_id: str = "api-agent"
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        "trace_id": trace_id,
        "span_id": span_id,
        "agent_id": agent_id,
        "event_type": "terminate" if symbol == "terminate" else "tool_call",
        "symbol": symbol,
        "raw_symbol": symbol,
        "attributes": {
            "param_schema_hash": "hash-api",
            "status": "success",
        },
        "timestamp": "2026-09-07T12:00:00Z",
        "framework": "mcp",
        "framework_schema_version": "1.0",
        "adapter_version": "1.0.0",
        "sequence_no": seq_no,
        "status": "success",
        "provenance": {
            "timestamp_source": "framework",
            "ingested_at": "2026-09-07T12:00:00Z",
        },
    }


def test_api_health_and_metrics():
    app = create_app(db_path=":memory:")
    client = TestClient(app)

    # Health
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Metrics
    m_resp = client.get("/metrics")
    assert m_resp.status_code == 200
    assert "trace_events_ingested_total" in m_resp.text
    assert "trace_verification_latency_avg_ms" in m_resp.text

    # Dashboard HTML
    dash_resp = client.get("/dashboard")
    assert dash_resp.status_code == 200
    assert "TRACE" in dash_resp.text
    assert "Automata Graph" in dash_resp.text

    # Demo Seed
    seed_resp = client.post("/v1/demo/seed")
    assert seed_resp.status_code == 200
    assert seed_resp.json()["status"] == "seeded"


def test_api_events_ingestion_and_streaming_verify(tmp_path):
    db_file = str(tmp_path / "api_test.sqlite")
    app = create_app(db_path=db_file)
    client = TestClient(app)

    # 1. Ingest batch of events
    t_id = str(uuid4())
    s_id = "span-api"
    events = [
        _create_sample_record(t_id, s_id, "start", 0),
        _create_sample_record(t_id, s_id, "process", 1),
        _create_sample_record(t_id, s_id, "terminate", 2),
    ]

    ingest_resp = client.post("/v1/events", json=events)
    assert ingest_resp.status_code == 200
    data = ingest_resp.json()
    assert data["accepted"] == 3
    assert data["rejected"] == 0

    # 2. Train and promote model for agent 'api-agent'
    pdfa = PDFA(q0="q0")
    pdfa.add_transition("q0", "start", "q1", frequency=10)
    pdfa.add_transition("q1", "process", "q2", frequency=10)
    pdfa.add_transition("q2", "terminate", "q2", frequency=10)
    pdfa.mark_final("q2")

    # Save and promote in model repository connected to same db
    repo = ModelRepository(db_file)
    m_id = repo.save_model("api-agent", pdfa, training_corpus_hash="hash-api", learner_config={"heuristic": "alergia"})
    repo.validate_model(m_id)
    repo.promote_model(m_id)

    # 3. Verify event via /v1/verify
    verify_payload = {
        "event": _create_sample_record(t_id, s_id, "start", 0),
        "mode": "observe",
    }
    ver_resp = client.post("/v1/verify", json=verify_payload)
    assert ver_resp.status_code == 200
    ver_data = ver_resp.json()
    assert ver_data["allowed"] is True
    assert len(ver_data["classification"]) == 0


def test_api_feedback_lifecycle(tmp_path):
    db_file = str(tmp_path / "feedback_test.sqlite")
    app = create_app(db_path=db_file)
    client = TestClient(app)

    v_id = str(uuid4())
    payload = {
        "violation_id": v_id,
        "feedback_type": "approve",
        "reviewer": "qa-lead",
        "comment": "Safe operation in staging",
    }

    # Submit feedback
    resp = client.post("/v1/feedback", json=payload)
    assert resp.status_code == 200
    fb_data = resp.json()
    assert fb_data["violation_id"] == v_id
    assert fb_data["feedback_type"] == "approve"

    # List feedback
    list_resp = client.get("/v1/feedback")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["reviewer"] == "qa-lead"


@pytest.mark.asyncio
async def test_async_queue_consumer():
    consumer = AsyncQueueConsumer()
    received_records = []

    def handler(record: CESRecord):
        received_records.append(record)

    await consumer.start(handler)

    # Push valid record
    ev1 = _create_sample_record("trace-1", "span-1", "ping", 0)
    await consumer.put_event(ev1)

    # Push invalid record (triggers dead-letter queue)
    bad_ev = {"schema_version": "1.0", "missing": "fields"}
    await consumer.put_event(bad_ev)

    # Allow event loop processing
    await asyncio.sleep(0.2)
    await consumer.stop()

    assert len(received_records) == 1
    assert received_records[0].symbol == "ping"
    assert len(consumer.dead_letter_queue) == 1


def test_benchmark_suite_rq1_to_rq6():
    suite = BenchmarkSuite(seed=123)
    summary = suite.run_all()

    assert len(summary.rq1.sample_sizes) == 4
    assert summary.rq2.f1 > 0.8
    assert summary.rq3.budget_met is True
    assert summary.rq3.p99_latency_ms < 5.0
    assert summary.rq4.policy_enforcement_accuracy == 1.0
    assert summary.rq5.drift_detected is True
    assert summary.rq6.reduction_percentage > 50.0

    rendered = summary.render_markdown()
    assert "RQ1: Learning Sample Complexity" in rendered
    assert "Budget Met (<5ms): **YES**" in rendered
