"""
FastAPI REST API Server for TRACE Runtime (PRD §30.2, §35).
Provides high-throughput ingestion, low-latency streaming verification, model management,
HITL feedback triage, and telemetry metrics.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from trace.corpus.repository import TraceRepository
from trace.corpus.store import TraceStore
from trace.feedback.engine import FeedbackEngine, FeedbackType, RelationalFeedbackStore
from trace.ingestion.pipeline import IngestionPipeline
from trace.models.repository import ModelLifecycleStatus, ModelRepository
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.schema.models import CESRecord, validate_ces_record
from trace.storage.postgres import PostgresStore
from trace.verification.session_cache import (
    InMemorySessionCache,
    SessionCache,
)
from trace.verification.verifier import RuntimeVerifier, VerificationResponse


class VerifyRequest(BaseModel):
    event: CESRecord
    mode: Literal["observe", "gate"] = "observe"
    policy_source: Optional[str] = None
    role: Optional[str] = None


class IngestionResponse(BaseModel):
    accepted: int
    rejected: int
    duplicates: int
    errors: List[str] = Field(default_factory=list)


class FeedbackSubmissionRequest(BaseModel):
    violation_id: str
    feedback_type: FeedbackType
    reviewer: str
    comment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TelemetryMetrics:
    """Thread-safe Prometheus-compatible in-memory metric counters."""

    def __init__(self):
        self.events_ingested_total: int = 0
        self.events_verified_total: int = 0
        self.violations_total: Dict[str, int] = {
            "structural": 0,
            "statistical": 0,
            "policy": 0,
            "hierarchical": 0,
        }
        self.verification_latency_sum_ms: float = 0.0

    def render_prometheus(self) -> str:
        lines = [
            "# HELP trace_events_ingested_total Total number of CES events ingested",
            "# TYPE trace_events_ingested_total counter",
            f"trace_events_ingested_total {self.events_ingested_total}",
            "# HELP trace_events_verified_total Total number of events verified",
            "# TYPE trace_events_verified_total counter",
            f"trace_events_verified_total {self.events_verified_total}",
            "# HELP trace_violations_detected_total Total number of violations detected by type",
            "# TYPE trace_violations_detected_total counter",
        ]
        for v_type, count in self.violations_total.items():
            lines.append(f'trace_violations_detected_total{{type="{v_type}"}} {count}')

        avg_latency = (
            (self.verification_latency_sum_ms / self.events_verified_total)
            if self.events_verified_total > 0
            else 0.0
        )
        lines.extend([
            "# HELP trace_verification_latency_avg_ms Average verification latency in milliseconds",
            "# TYPE trace_verification_latency_avg_ms gauge",
            f"trace_verification_latency_avg_ms {avg_latency:.4f}",
        ])
        return "\n".join(lines) + "\n"


def create_app(
    db_path: str = ":memory:",
    session_cache: Optional[SessionCache] = None,
) -> FastAPI:
    """Factory creating configured FastAPI instance with attached services."""
    app = FastAPI(
        title="TRACE Runtime API",
        version="0.1.0",
        description="Trace-based Runtime Automata for Compliance and Enforcement API",
    )

    # State dependencies
    trace_repo = PostgresStore(f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite:///:memory:")
    model_repo = ModelRepository(db_path)
    feedback_engine = FeedbackEngine(
        store=RelationalFeedbackStore(f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite:///:memory:"),
        model_repo=model_repo,
    )
    cache = session_cache or InMemorySessionCache()
    metrics = TelemetryMetrics()

    # Active verifier cache: agent_id -> RuntimeVerifier
    verifiers: Dict[str, RuntimeVerifier] = {}

    @app.get("/health", tags=["System"])
    def health_check():
        return {
            "status": "ok",
            "version": "0.1.0",
            "db": db_path,
        }

    @app.get("/metrics", response_class=PlainTextResponse, tags=["System"])
    def get_metrics():
        return metrics.render_prometheus()

    @app.post("/v1/events", response_model=IngestionResponse, tags=["Ingestion"])
    def ingest_events(events: List[Dict[str, Any]]):
        """Ingest a batch of Canonical Event Schema records."""
        accepted_records = []
        errors = []

        for item in events:
            val = validate_ces_record(item)
            if not val.is_valid:
                errors.extend(val.errors)
                continue
            try:
                record = CESRecord.model_validate(item)
                accepted_records.append(record)
            except Exception as e:
                errors.append(str(e))

        if accepted_records:
            trace_repo.insert_events_batch(accepted_records)
            metrics.events_ingested_total += len(accepted_records)

        return IngestionResponse(
            accepted=len(accepted_records),
            rejected=len(errors),
            duplicates=0,
            errors=errors,
        )

    @app.post("/v1/verify", response_model=VerificationResponse, tags=["Verification"])
    def verify_streaming_event(req: VerifyRequest):
        """Verify an incoming execution event against the agent's active model and policy."""
        t_start = time.perf_counter()
        agent_id = req.event.agent_id

        # Resolve verifier
        if agent_id not in verifiers:
            active_model = model_repo.get_active_model(agent_id)
            if not active_model:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No active PDFA model registered for agent '{agent_id}'",
                )

            policy_dfa = None
            if req.policy_source:
                ast = PolicyParser.parse(req.policy_source)
                policy_dfa = PolicyCompiler.compile(ast)

            verifiers[agent_id] = RuntimeVerifier(
                pdfa=active_model["pdfa"],
                policy_dfa=policy_dfa,
                mode=req.mode,
            )

        verifier = verifiers[agent_id]
        resp = verifier.verify_event(req.event, session_cache=cache)

        # Update telemetry
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        metrics.events_verified_total += 1
        metrics.verification_latency_sum_ms += latency_ms

        for flag in resp.classification:
            if flag in metrics.violations_total:
                metrics.violations_total[flag] += 1

        return resp

    @app.get("/v1/models", tags=["Models"])
    def list_models(agent_id: Optional[str] = Query(None)):
        """List learned models and current promotion status."""
        return model_repo.list_models(agent_id=agent_id)

    @app.get("/v1/models/{model_id}", tags=["Models"])
    def get_model(model_id: str):
        """Inspect a specific PDFA model definition."""
        m = model_repo.get_model(model_id)
        if not m:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model_id}' not found",
            )
        pdfa = m["pdfa"]
        return {
            "model_id": m["model_id"],
            "agent_id": m["agent_id"],
            "version": m["model_version"],
            "status": m["status"],
            "states_count": len(pdfa.states),
            "alphabet": sorted(list(pdfa.alphabet)),
            "transitions_count": len(pdfa.delta),
        }

    @app.post("/v1/models/{model_id}/promote", tags=["Models"])
    def promote_model(model_id: str, activate: bool = True):
        """Promote a candidate model into active production deployment."""
        try:
            res = feedback_engine.review_candidate_model(
                model_id=model_id,
                action="approve",
                reviewer="api-operator",
            )
            if activate:
                feedback_engine.activate_promoted_model(model_id)
                res["status"] = ModelLifecycleStatus.ACTIVE.value
            return res
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @app.post("/v1/feedback", tags=["Feedback"])
    def submit_feedback(req: FeedbackSubmissionRequest):
        """Submit operator review triage on a detected runtime violation."""
        record = feedback_engine.record_feedback(
            violation_id=req.violation_id,
            feedback_type=req.feedback_type,
            reviewer=req.reviewer,
            comment=req.comment,
            metadata=req.metadata,
        )
        return record

    @app.get("/v1/feedback", tags=["Feedback"])
    def list_feedback(applied: Optional[bool] = None):
        """List recorded human review decisions."""
        return feedback_engine.list_feedback(applied=applied)

    return app
