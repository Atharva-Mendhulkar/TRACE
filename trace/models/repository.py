"""
Model Repository & Lifecycle Management (PRD §14.7, §23).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import uuid4

from trace.models.pdfa import PDFA

import enum

class ModelLifecycleStatus(str, enum.Enum):
    TRAINING = "TRAINING"
    VALIDATION = "VALIDATION"
    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"

ModelStatus = Literal[
    "TRAINING",
    "VALIDATION",
    "CANDIDATE",
    "PROMOTED",
    "ACTIVE",
    "SUPERSEDED",
    "ARCHIVED",
]


class ModelRepository:
    """Manages storage, validation, versioning, and promotion of PDFA models."""

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self.db_path = str(db_path)
        self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False) if self.db_path == ":memory:" else None
        if self._shared_conn:
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._shared_conn:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS models (
                model_id TEXT PRIMARY KEY,
                model_version INTEGER NOT NULL,
                agent_id TEXT NOT NULL,
                role TEXT,
                taxonomy_version INTEGER NOT NULL DEFAULT 1,
                training_corpus_hash TEXT NOT NULL,
                learner_config TEXT NOT NULL,
                evaluation_metrics TEXT,
                status TEXT NOT NULL,
                creator TEXT NOT NULL,
                pdfa_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promoted_at TEXT,
                activated_at TEXT,
                superseded_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_models_agent_status 
                ON models (agent_id, status);
            CREATE INDEX IF NOT EXISTS idx_models_role_status 
                ON models (role, status);
            """)
            try:
                conn.execute("ALTER TABLE models ADD COLUMN role TEXT")
            except sqlite3.OperationalError:
                pass

    def save_model(
        self,
        agent_id: str,
        pdfa: PDFA,
        training_corpus_hash: str,
        learner_config: Dict[str, Any],
        creator: str = "system",
        taxonomy_version: int = 1,
        role: Optional[str] = None,
    ) -> str:
        """Store a newly trained PDFA in VALIDATION status."""
        model_id = str(uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(model_version), 0) + 1 FROM models WHERE agent_id = ?",
                (agent_id,),
            )
            model_version = cur.fetchone()[0]

            pdfa_json = json.dumps(pdfa.to_dict())
            learner_json = json.dumps(learner_config)

            cur.execute("""
                INSERT INTO models (
                    model_id, model_version, agent_id, role, taxonomy_version,
                    training_corpus_hash, learner_config, status,
                    creator, pdfa_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'VALIDATION', ?, ?, ?)
            """, (
                model_id, model_version, agent_id, role, taxonomy_version,
                training_corpus_hash, learner_json, creator, pdfa_json, now
            ))
            conn.commit()

        return model_id

    def validate_model(
        self,
        model_id: str,
        training_alphabet: Optional[List[str]] = None,
        held_out_corpus: Optional[List[List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Run PRD §14.7 validation checks on model:
        1. Determinism check
        2. Coverage check
        3. Held-out likelihood check
        """
        model_info = self.get_model(model_id)
        if not model_info:
            raise ValueError(f"Model {model_id} not found")

        pdfa = model_info["pdfa"]
        errors: List[str] = []

        # 1. Determinism check (by definition in our PDFA dictionary delta, but check explicitly)
        seen_pairs = set()
        is_deterministic = True
        for (st, sym) in pdfa.delta.keys():
            if (st, sym) in seen_pairs:
                is_deterministic = False
                errors.append(f"Non-deterministic transition detected at state '{st}' for symbol '{sym}'")
            seen_pairs.add((st, sym))

        # 2. Coverage check
        missing_symbols = []
        if training_alphabet:
            for sym in training_alphabet:
                if sym not in pdfa.alphabet:
                    missing_symbols.append(sym)
            if missing_symbols:
                errors.append(f"Model missing symbols from training alphabet: {missing_symbols}")

        # 3. Held-out evaluation
        held_out_nll = None
        if held_out_corpus:
            nlls = []
            for trace in held_out_corpus:
                m_nll, _, has_struct = pdfa.compute_trace_mean_nll(trace)
                if not has_struct and m_nll < float("inf"):
                    nlls.append(m_nll)
            held_out_nll = sum(nlls) / len(nlls) if nlls else None

        passed = len(errors) == 0
        new_status = "CANDIDATE" if passed else "VALIDATION"

        metrics = {
            "is_deterministic": is_deterministic,
            "state_count": len(pdfa.states),
            "transition_count": len(pdfa.delta),
            "alphabet_size": len(pdfa.alphabet),
            "missing_symbols": missing_symbols,
            "held_out_mean_nll": held_out_nll,
            "validation_passed": passed,
            "errors": errors,
        }

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE models 
                SET status = ?, evaluation_metrics = ?
                WHERE model_id = ?
            """, (new_status, json.dumps(metrics), model_id))
            conn.commit()

        return metrics

    def promote_model(self, model_id: str, approver: str = "human_approver") -> bool:
        """
        Promote a CANDIDATE model to ACTIVE (PRD §23.3).
        Marks previous ACTIVE model as SUPERSEDED.
        """
        model_info = self.get_model(model_id)
        if not model_info:
            return False
        if model_info["status"] != "CANDIDATE":
            raise ValueError(f"Model must be in CANDIDATE status to be promoted, current: {model_info['status']}")

        agent_id = model_info["agent_id"]
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.cursor()
            # Supersede currently active model
            cur.execute("""
                UPDATE models 
                SET status = 'SUPERSEDED', superseded_at = ?
                WHERE agent_id = ? AND status = 'ACTIVE'
            """, (now, agent_id))

            # Promote candidate to active
            cur.execute("""
                UPDATE models 
                SET status = 'ACTIVE', promoted_at = ?, activated_at = ?
                WHERE model_id = ?
            """, (now, now, model_id))
            conn.commit()

        return True

    def activate_model(self, model_id: str) -> bool:
        """Activate a model into production, marking existing active models as SUPERSEDED."""
        model_info = self.get_model(model_id)
        if not model_info:
            return False

        agent_id = model_info["agent_id"]
        role = model_info.get("role")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.cursor()
            if role:
                cur.execute("""
                    UPDATE models 
                    SET status = 'SUPERSEDED', superseded_at = ?
                    WHERE role = ? AND status = 'ACTIVE' AND model_id != ?
                """, (now, role, model_id))
            else:
                cur.execute("""
                    UPDATE models 
                    SET status = 'SUPERSEDED', superseded_at = ?
                    WHERE agent_id = ? AND status = 'ACTIVE' AND model_id != ?
                """, (now, agent_id, model_id))

            cur.execute("""
                UPDATE models 
                SET status = 'ACTIVE', activated_at = ?
                WHERE model_id = ?
            """, (now, model_id))
            conn.commit()

        return True

    def rollback_model(self, agent_id: str, target_model_id: str, reason: str) -> bool:
        """Rollback active model to a previous model version (PRD §23.4)."""
        target = self.get_model(target_model_id)
        if not target or target["agent_id"] != agent_id:
            return False

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE models 
                SET status = 'SUPERSEDED', superseded_at = ?
                WHERE agent_id = ? AND status = 'ACTIVE'
            """, (now, agent_id))

            cur.execute("""
                UPDATE models 
                SET status = 'ACTIVE', activated_at = ?
                WHERE model_id = ?
            """, (now, target_model_id))
            conn.commit()

        return True

    def get_active_model(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM models WHERE agent_id = ? AND status = 'ACTIVE' ORDER BY model_version DESC LIMIT 1",
                (agent_id,)
            )
            row = cur.fetchone()
            return self._row_to_model_dict(row) if row else None

    def get_active_model_for_role(self, role: str) -> Optional[Dict[str, Any]]:
        """Return active or promoted model for a delegated role (PRD §16.2)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM models WHERE role = ? AND status = 'ACTIVE' ORDER BY model_version DESC LIMIT 1",
                (role,)
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "SELECT * FROM models WHERE role = ? AND status IN ('PROMOTED', 'CANDIDATE') ORDER BY model_version DESC LIMIT 1",
                    (role,)
                )
                row = cur.fetchone()
            return self._row_to_model_dict(row) if row else None

    def get_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM models WHERE model_id = ?", (model_id,))
            row = cur.fetchone()
            return self._row_to_model_dict(row) if row else None

    def list_models(
        self,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            query = "SELECT * FROM models WHERE 1=1"
            params: List[Any] = []
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            if role:
                query += " AND role = ?"
                params.append(role)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY model_version DESC"
            cur.execute(query, params)
            return [self._row_to_model_dict(r) for r in cur.fetchall()]

    def _row_to_model_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        pdfa_data = json.loads(row["pdfa_json"])
        pdfa = PDFA.from_dict(pdfa_data)
        metrics = json.loads(row["evaluation_metrics"]) if row["evaluation_metrics"] else {}
        config = json.loads(row["learner_config"]) if row["learner_config"] else {}
        role = row["role"] if "role" in row.keys() else None
        return {
            "model_id": row["model_id"],
            "model_version": row["model_version"],
            "agent_id": row["agent_id"],
            "role": role,
            "taxonomy_version": row["taxonomy_version"],
            "training_corpus_hash": row["training_corpus_hash"],
            "learner_config": config,
            "evaluation_metrics": metrics,
            "status": row["status"],
            "creator": row["creator"],
            "created_at": row["created_at"],
            "promoted_at": row["promoted_at"],
            "activated_at": row["activated_at"],
            "pdfa": pdfa,
        }
