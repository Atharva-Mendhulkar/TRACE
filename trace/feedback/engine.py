"""
Human-in-the-Loop (HITL) Feedback & Model Promotion Engine (PRD M10, §23.4, §29).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import datetime
import json
import logging
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from trace.models.repository import ModelLifecycleStatus, ModelRepository

logger = logging.getLogger("trace.feedback")

FeedbackType = Literal["approve", "reject", "override_transition"]


class FeedbackRecord(BaseModel):
    """Record of human feedback on a detected violation (PRD §23.4, §29)."""

    feedback_id: str = Field(default_factory=lambda: str(uuid4()))
    violation_id: str
    feedback_type: FeedbackType
    reviewer: str
    comment: Optional[str] = None
    applied: bool = False
    applied_in_model_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class FeedbackStore(ABC):
    """Abstract interface for storing and retrieving feedback records."""

    @abstractmethod
    def save_feedback(self, record: FeedbackRecord) -> None:
        pass

    @abstractmethod
    def get_feedback(self, feedback_id: str) -> Optional[FeedbackRecord]:
        pass

    @abstractmethod
    def get_feedback_for_violation(self, violation_id: str) -> List[FeedbackRecord]:
        pass

    @abstractmethod
    def list_feedback(self, applied: Optional[bool] = None) -> List[FeedbackRecord]:
        pass

    @abstractmethod
    def mark_applied(self, feedback_id: str, applied_in_model_id: str) -> None:
        pass


class RelationalFeedbackStore(FeedbackStore):
    """SQLAlchemy-backed feedback store compatible with PostgreSQL and SQLite."""

    def __init__(self, db_url: str = "sqlite:///:memory:", engine: Optional[Engine] = None):
        self.engine = engine or create_engine(db_url, echo=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS feedback (
                        feedback_id TEXT PRIMARY KEY,
                        violation_id TEXT NOT NULL,
                        feedback_type TEXT NOT NULL,
                        reviewer TEXT NOT NULL,
                        comment TEXT,
                        applied INTEGER NOT NULL DEFAULT 0,
                        applied_in_model_id TEXT,
                        metadata TEXT,
                        created_at TEXT NOT NULL
                    );
                    """
                )
            )

    def save_feedback(self, record: FeedbackRecord) -> None:
        with self.engine.begin() as conn:
            stmt = text(
                """
                INSERT INTO feedback (
                    feedback_id, violation_id, feedback_type, reviewer,
                    comment, applied, applied_in_model_id, metadata, created_at
                ) VALUES (
                    :f_id, :v_id, :f_type, :reviewer,
                    :comment, :applied, :model_id, :metadata, :created_at
                )
                """
            )
            conn.execute(
                stmt,
                {
                    "f_id": record.feedback_id,
                    "v_id": record.violation_id,
                    "f_type": record.feedback_type,
                    "reviewer": record.reviewer,
                    "comment": record.comment,
                    "applied": 1 if record.applied else 0,
                    "model_id": record.applied_in_model_id,
                    "metadata": json.dumps(record.metadata),
                    "created_at": record.created_at,
                },
            )

    def get_feedback(self, feedback_id: str) -> Optional[FeedbackRecord]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT feedback_id, violation_id, feedback_type, reviewer,
                           comment, applied, applied_in_model_id, metadata, created_at
                    FROM feedback WHERE feedback_id = :f_id
                    """
                ),
                {"f_id": feedback_id},
            ).fetchone()

        if not row:
            return None
        return self._row_to_record(row)

    def get_feedback_for_violation(self, violation_id: str) -> List[FeedbackRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT feedback_id, violation_id, feedback_type, reviewer,
                           comment, applied, applied_in_model_id, metadata, created_at
                    FROM feedback WHERE violation_id = :v_id
                    ORDER BY created_at ASC
                    """
                ),
                {"v_id": violation_id},
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_feedback(self, applied: Optional[bool] = None) -> List[FeedbackRecord]:
        query = (
            "SELECT feedback_id, violation_id, feedback_type, reviewer, "
            "comment, applied, applied_in_model_id, metadata, created_at FROM feedback"
        )
        params: Dict[str, Any] = {}
        if applied is not None:
            query += " WHERE applied = :app"
            params["app"] = 1 if applied else 0
        query += " ORDER BY created_at DESC"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def mark_applied(self, feedback_id: str, applied_in_model_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE feedback
                    SET applied = 1, applied_in_model_id = :m_id
                    WHERE feedback_id = :f_id
                    """
                ),
                {"m_id": applied_in_model_id, "f_id": feedback_id},
            )

    def _row_to_record(self, row: Any) -> FeedbackRecord:
        meta = json.loads(row[7]) if row[7] else {}
        return FeedbackRecord(
            feedback_id=row[0],
            violation_id=row[1],
            feedback_type=row[2],
            reviewer=row[3],
            comment=row[4],
            applied=bool(row[5]),
            applied_in_model_id=row[6],
            metadata=meta,
            created_at=row[8],
        )


class FeedbackEngine:
    """
    Orchestrates human-in-the-loop review of violations and model candidate promotion.
    """

    def __init__(
        self,
        store: Optional[FeedbackStore] = None,
        model_repo: Optional[ModelRepository] = None,
    ):
        self.store = store or RelationalFeedbackStore()
        self.model_repo = model_repo

    def record_feedback(
        self,
        violation_id: str,
        feedback_type: FeedbackType,
        reviewer: str,
        comment: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackRecord:
        """
        Record reviewer feedback on a violation.
        - approve: false positive, flag sequence as acceptable.
        - reject: true positive, confirm anomaly/violation.
        - override_transition: explicit operator authorization for the transition.
        """
        record = FeedbackRecord(
            violation_id=violation_id,
            feedback_type=feedback_type,
            reviewer=reviewer,
            comment=comment,
            metadata=metadata or {},
        )
        self.store.save_feedback(record)
        logger.info(
            "Recorded feedback %s on violation %s by %s: %s",
            record.feedback_id,
            violation_id,
            reviewer,
            feedback_type,
        )
        return record

    def list_feedback(self, applied: Optional[bool] = None) -> List[FeedbackRecord]:
        return self.store.list_feedback(applied=applied)

    def get_feedback(self, feedback_id: str) -> Optional[FeedbackRecord]:
        return self.store.get_feedback(feedback_id)

    def review_candidate_model(
        self,
        model_id: str,
        action: Literal["approve", "reject"],
        reviewer: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Review a model in CANDIDATE status.
        If approved: advances model status to PROMOTED.
        If rejected: model remains unpromoted.
        """
        if not self.model_repo:
            raise ValueError("ModelRepository not configured for FeedbackEngine")

        if action == "approve":
            self.model_repo.promote_model(model_id)
            return {
                "model_id": model_id,
                "action": "approved",
                "status": ModelLifecycleStatus.PROMOTED.value,
                "reviewer": reviewer,
                "comment": comment,
            }
        else:
            return {
                "model_id": model_id,
                "action": "rejected",
                "status": ModelLifecycleStatus.CANDIDATE.value,
                "reviewer": reviewer,
                "comment": comment,
            }

    def activate_promoted_model(self, model_id: str) -> Dict[str, Any]:
        """
        Hot-reload and activate a PROMOTED model into production (ACTIVE status),
        superseding any previously active model for that agent/role.
        """
        if not self.model_repo:
            raise ValueError("ModelRepository not configured for FeedbackEngine")

        self.model_repo.activate_model(model_id)
        return {
            "model_id": model_id,
            "status": ModelLifecycleStatus.ACTIVE.value,
            "activated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
