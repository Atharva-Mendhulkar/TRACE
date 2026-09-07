"""
PostgreSQL + pgvector Storage Backend (PRD §11.1, §12.3, §29).
Implements TraceRepository, ModelRepository, and pgvector ANN centroid lookup.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4
import numpy as np

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from trace.models.pdfa import PDFA
from trace.corpus.repository import TraceRepository
from trace.models.repository import ModelLifecycleStatus, ModelRepository
from trace.schema.models import (
    CESRecord,
    EventAttributes,
    ErrorInfo,
    ProvenanceInfo,
    ViolationRecord,
)

logger = logging.getLogger("trace.storage.postgres")


class PostgresStore(TraceRepository, ModelRepository):
    """
    Production-grade relational store using PostgreSQL and pgvector,
    satisfying both TraceRepository and ModelRepository protocols.
    Supports SQLite fallback for testing environments.
    """

    def __init__(self, db_url: str = "sqlite:///:memory:", engine: Optional[Engine] = None):
        self.db_url = db_url
        self.engine = engine or create_engine(db_url, echo=False)
        self.is_postgres = "postgresql" in self.engine.dialect.name.lower()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they do not already exist."""
        with self.engine.begin() as conn:
            if self.is_postgres:
                try:
                    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                except Exception as e:
                    logger.warning("Could not create postgres extensions: %s", e)

            # Events
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        span_id TEXT NOT NULL,
                        parent_span_id TEXT,
                        agent_id TEXT NOT NULL,
                        role TEXT,
                        depth INTEGER NOT NULL DEFAULT 0,
                        framework TEXT NOT NULL,
                        framework_schema_version TEXT NOT NULL,
                        adapter_version TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        raw_symbol TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        sequence_no BIGINT NOT NULL,
                        status TEXT NOT NULL,
                        param_schema_hash TEXT NOT NULL,
                        taxonomy_version INTEGER NOT NULL DEFAULT 1,
                        timestamp TEXT NOT NULL,
                        ingested_at TEXT NOT NULL,
                        payload TEXT
                    );
                    """
                )
            )

            # Models
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS models (
                        model_id TEXT PRIMARY KEY,
                        model_version INTEGER NOT NULL,
                        agent_id TEXT NOT NULL,
                        role TEXT,
                        taxonomy_version INTEGER NOT NULL DEFAULT 1,
                        training_corpus_hash TEXT NOT NULL,
                        training_window_start TEXT NOT NULL,
                        training_window_end TEXT NOT NULL,
                        learner_config TEXT NOT NULL,
                        evaluation_metrics TEXT,
                        status TEXT NOT NULL,
                        creator TEXT NOT NULL,
                        automaton_data TEXT NOT NULL,
                        trained_at TEXT,
                        validated_at TEXT,
                        promoted_at TEXT,
                        activated_at TEXT,
                        superseded_at TEXT
                    );
                    """
                )
            )

            # Centroids (pgvector if postgres, TEXT array if sqlite)
            centroid_col_type = "VECTOR(768)" if self.is_postgres else "TEXT"
            try:
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE IF NOT EXISTS symbol_centroids (
                            taxonomy_version INTEGER NOT NULL,
                            canonical_symbol TEXT NOT NULL,
                            centroid {centroid_col_type},
                            PRIMARY KEY (taxonomy_version, canonical_symbol)
                        );
                        """
                    )
                )
            except Exception:
                # If vector extension is missing in postgres, fall back to TEXT
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS symbol_centroids (
                            taxonomy_version INTEGER NOT NULL,
                            canonical_symbol TEXT NOT NULL,
                            centroid TEXT,
                            PRIMARY KEY (taxonomy_version, canonical_symbol)
                        );
                        """
                    )
                )

            # Violations
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS violations (
                        violation_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        role TEXT,
                        classification TEXT NOT NULL,
                        model_id TEXT,
                        policy_id TEXT,
                        explanation TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
            )

            # Live Verification State (Hot path stream caching)
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS verification_state (
                        trace_id TEXT NOT NULL,
                        span_id TEXT NOT NULL,
                        model_id TEXT,
                        policy_id TEXT,
                        current_learned_state TEXT NOT NULL,
                        current_policy_state TEXT NOT NULL,
                        running_mean_nll REAL NOT NULL DEFAULT 0.0,
                        running_events_count INTEGER NOT NULL DEFAULT 0,
                        last_event_time TEXT NOT NULL,
                        PRIMARY KEY (trace_id, span_id)
                    );
                    """
                )
            )

    # -------------------------------------------------------------------------
    # TraceRepository Implementation
    # -------------------------------------------------------------------------

    def insert_event(self, event: CESRecord) -> None:
        self.insert_events_batch([event])

    def insert_events_batch(self, events: List[CESRecord]) -> None:
        if not events:
            return

        with self.engine.begin() as conn:
            stmt = text(
                """
                INSERT INTO events (
                    event_id, trace_id, span_id, parent_span_id, agent_id, role, depth,
                    framework, framework_schema_version, adapter_version, event_type,
                    raw_symbol, symbol, sequence_no, status, param_schema_hash,
                    taxonomy_version, timestamp, ingested_at, payload
                ) VALUES (
                    :event_id, :trace_id, :span_id, :parent_span_id, :agent_id, :role, :depth,
                    :framework, :framework_schema_version, :adapter_version, :event_type,
                    :raw_symbol, :symbol, :sequence_no, :status, :param_schema_hash,
                    :taxonomy_version, :timestamp, :ingested_at, :payload
                )
                """
            )
            params = [
                {
                    "event_id": str(e.event_id),
                    "trace_id": str(e.trace_id),
                    "span_id": str(e.span_id),
                    "parent_span_id": str(e.parent_span_id) if e.parent_span_id else None,
                    "agent_id": e.agent_id,
                    "role": e.role,
                    "depth": e.depth,
                    "framework": e.framework,
                    "framework_schema_version": e.framework_schema_version,
                    "adapter_version": e.adapter_version,
                    "event_type": e.event_type,
                    "raw_symbol": e.raw_symbol,
                    "symbol": e.symbol,
                    "sequence_no": e.sequence_no,
                    "status": e.status,
                    "param_schema_hash": e.attributes.param_schema_hash,
                    "taxonomy_version": 1,
                    "timestamp": e.timestamp,
                    "ingested_at": e.provenance.ingested_at,
                    "payload": json.dumps(e.model_dump(mode="json")),
                }
                for e in events
            ]
            conn.execute(stmt, params)

    def get_trace(self, trace_id: str) -> List[CESRecord]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text("SELECT payload FROM events WHERE trace_id = :t_id ORDER BY sequence_no ASC"),
                {"t_id": str(trace_id)},
            )
            return [CESRecord.model_validate_json(row[0]) for row in res.fetchall()]

    def get_corpus(self, agent_id: str, include_truncated: bool = False) -> List[List[str]]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                    SELECT trace_id, event_type, symbol, role
                    FROM events
                    WHERE agent_id = :a_id AND depth = 0
                    ORDER BY trace_id, sequence_no ASC
                    """
                ),
                {"a_id": agent_id},
            )
            rows = res.fetchall()

        traces: Dict[str, List[str]] = {}
        trace_has_terminate: Dict[str, bool] = {}

        for t_id, ev_type, sym, role in rows:
            traces.setdefault(t_id, [])
            trace_has_terminate.setdefault(t_id, False)

            if ev_type == "delegate":
                target = role or "unknown"
                traces[t_id].append(f"delegate({target})")
            elif ev_type == "terminate":
                trace_has_terminate[t_id] = True
                traces[t_id].append("terminate")
            else:
                traces[t_id].append(sym)

        corpus: List[List[str]] = []
        for t_id, symbols in traces.items():
            if include_truncated or trace_has_terminate[t_id]:
                if symbols:
                    corpus.append(symbols)

        return corpus

    def get_role_corpus(self, role: str, include_truncated: bool = False) -> List[List[str]]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                    SELECT trace_id, span_id, event_type, symbol, role
                    FROM events
                    WHERE role = :r AND (depth > 0 OR parent_span_id IS NOT NULL)
                    ORDER BY trace_id, span_id, sequence_no ASC
                    """
                ),
                {"r": role},
            )
            rows = res.fetchall()

        spans: Dict[str, List[str]] = {}
        span_has_terminate: Dict[str, bool] = {}

        for t_id, s_id, ev_type, sym, r in rows:
            key = f"{t_id}:{s_id}"
            spans.setdefault(key, [])
            span_has_terminate.setdefault(key, False)

            if ev_type == "delegate":
                target = r or "unknown"
                spans[key].append(f"delegate({target})")
            elif ev_type in ("terminate", "tool_result") and (sym == "return" or ev_type == "terminate"):
                span_has_terminate[key] = True
                spans[key].append("return")
            else:
                spans[key].append(sym)

        corpus: List[List[str]] = []
        for key, symbols in spans.items():
            if include_truncated or span_has_terminate[key]:
                if symbols:
                    corpus.append(symbols)

        return corpus

    def get_delegated_traces(self, parent_span_id: str) -> List[CESRecord]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                    SELECT payload FROM events
                    WHERE parent_span_id = :ps_id
                    ORDER BY sequence_no ASC
                    """
                ),
                {"ps_id": str(parent_span_id)},
            )
            return [CESRecord.model_validate_json(row[0]) for row in res.fetchall()]

    # -------------------------------------------------------------------------
    # ModelRepository Implementation
    # -------------------------------------------------------------------------

    def save_model(
        self,
        agent_id: str,
        automaton: PDFA,
        metrics: Optional[Dict[str, Any]] = None,
        role: Optional[str] = None,
    ) -> str:
        model_id = str(uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metrics = metrics or {}

        automaton_dict = automaton.to_dict()
        automaton_dict["role"] = role

        with self.engine.begin() as conn:
            stmt = text(
                """
                INSERT INTO models (
                    model_id, model_version, agent_id, role, taxonomy_version,
                    training_corpus_hash, training_window_start, training_window_end,
                    learner_config, evaluation_metrics, status, creator,
                    automaton_data, trained_at
                ) VALUES (
                    :model_id, :model_version, :agent_id, :role, :taxonomy_version,
                    :training_corpus_hash, :start_time, :end_time,
                    :learner_config, :evaluation_metrics, :status, :creator,
                    :automaton_data, :trained_at
                )
                """
            )
            conn.execute(
                stmt,
                {
                    "model_id": model_id,
                    "model_version": 1,
                    "agent_id": agent_id,
                    "role": role,
                    "taxonomy_version": 1,
                    "training_corpus_hash": metrics.get("corpus_hash", "hash-0"),
                    "start_time": now,
                    "end_time": now,
                    "learner_config": json.dumps({"engine": metrics.get("engine", "pure_python")}),
                    "evaluation_metrics": json.dumps(metrics),
                    "status": ModelLifecycleStatus.TRAINING.value,
                    "creator": "trace-pipeline",
                    "automaton_data": json.dumps(automaton_dict),
                    "trained_at": now,
                },
            )

        return model_id

    def get_model(self, model_id: str) -> Optional[PDFA]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text("SELECT automaton_data FROM models WHERE model_id = :m_id"),
                {"m_id": str(model_id)},
            ).fetchone()

        if not res:
            return None
        return self._deserialize_automaton(res[0])

    def get_active_model(self, agent_id: str) -> Optional[PDFA]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                    SELECT automaton_data FROM models
                    WHERE agent_id = :a_id AND status = :s
                    ORDER BY model_version DESC LIMIT 1
                    """
                ),
                {"a_id": agent_id, "s": ModelLifecycleStatus.ACTIVE.value},
            ).fetchone()

        if not res:
            return None
        return self._deserialize_automaton(res[0])

    def get_active_model_for_role(self, role: str) -> Optional[PDFA]:
        with self.engine.connect() as conn:
            res = conn.execute(
                text(
                    """
                    SELECT automaton_data FROM models
                    WHERE role = :r AND status = :s
                    ORDER BY model_version DESC LIMIT 1
                    """
                ),
                {"r": role, "s": ModelLifecycleStatus.ACTIVE.value},
            ).fetchone()

        if not res:
            return None
        return self._deserialize_automaton(res[0])

    def validate_model(self, model_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE models
                    SET status = :s, validated_at = :t
                    WHERE model_id = :m_id
                    """
                ),
                {"s": ModelLifecycleStatus.CANDIDATE.value, "t": now, "m_id": str(model_id)},
            )

    def promote_model(self, model_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.engine.begin() as conn:
            res = conn.execute(
                text("SELECT status FROM models WHERE model_id = :m_id"),
                {"m_id": str(model_id)},
            ).fetchone()

            if not res or res[0] != ModelLifecycleStatus.CANDIDATE.value:
                raise ValueError(
                    f"Model {model_id} must be in CANDIDATE status to promote, currently {res[0] if res else 'None'}"
                )

            conn.execute(
                text(
                    """
                    UPDATE models
                    SET status = :s, promoted_at = :t
                    WHERE model_id = :m_id
                    """
                ),
                {"s": ModelLifecycleStatus.PROMOTED.value, "t": now, "m_id": str(model_id)},
            )

    def activate_model(self, model_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.engine.begin() as conn:
            # Supersede existing active models for the same agent/role
            row = conn.execute(
                text("SELECT agent_id, role FROM models WHERE model_id = :m_id"),
                {"m_id": str(model_id)},
            ).fetchone()

            if row:
                agent_id, role = row[0], row[1]
                if role:
                    conn.execute(
                        text(
                            """
                            UPDATE models SET status = :s, superseded_at = :t
                            WHERE role = :r AND status = :active AND model_id != :m_id
                            """
                        ),
                        {
                            "s": ModelLifecycleStatus.SUPERSEDED.value,
                            "t": now,
                            "r": role,
                            "active": ModelLifecycleStatus.ACTIVE.value,
                            "m_id": str(model_id),
                        },
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE models SET status = :s, superseded_at = :t
                            WHERE agent_id = :a AND status = :active AND model_id != :m_id
                            """
                        ),
                        {
                            "s": ModelLifecycleStatus.SUPERSEDED.value,
                            "t": now,
                            "a": agent_id,
                            "active": ModelLifecycleStatus.ACTIVE.value,
                            "m_id": str(model_id),
                        },
                    )

            conn.execute(
                text(
                    """
                    UPDATE models
                    SET status = :s, activated_at = :t
                    WHERE model_id = :m_id
                    """
                ),
                {"s": ModelLifecycleStatus.ACTIVE.value, "t": now, "m_id": str(model_id)},
            )

    def _deserialize_automaton(self, raw_json: str) -> PDFA:
        data = json.loads(raw_json)
        return PDFA.from_dict(data)

    # -------------------------------------------------------------------------
    # Centroid & Vector Similarity (PRD §12.3, §29)
    # -------------------------------------------------------------------------

    def save_centroid(self, taxonomy_version: int, canonical_symbol: str, centroid_vector: List[float]) -> None:
        with self.engine.begin() as conn:
            if self.is_postgres:
                # Format vector as string '[v1, v2, ...]' for pgvector
                vec_str = "[" + ",".join(str(x) for x in centroid_vector) + "]"
                conn.execute(
                    text(
                        """
                        INSERT INTO symbol_centroids (taxonomy_version, canonical_symbol, centroid)
                        VALUES (:ver, :sym, :vec)
                        ON CONFLICT (taxonomy_version, canonical_symbol)
                        DO UPDATE SET centroid = :vec
                        """
                    ),
                    {"ver": taxonomy_version, "sym": canonical_symbol, "vec": vec_str},
                )
            else:
                conn.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO symbol_centroids (taxonomy_version, canonical_symbol, centroid)
                        VALUES (:ver, :sym, :vec)
                        """
                    ),
                    {"ver": taxonomy_version, "sym": canonical_symbol, "vec": json.dumps(centroid_vector)},
                )

    def find_nearest_centroid(
        self, taxonomy_version: int, query_vector: List[float], threshold: float = 0.35
    ) -> Optional[Tuple[str, float]]:
        """
        Find nearest canonical symbol centroid for an embedding vector.
        Uses pgvector `<->` cosine distance on PostgreSQL, or numpy cosine distance on SQLite/fallback.
        """
        with self.engine.connect() as conn:
            if self.is_postgres:
                vec_str = "[" + ",".join(str(x) for x in query_vector) + "]"
                try:
                    res = conn.execute(
                        text(
                            """
                            SELECT canonical_symbol, centroid <-> :vec AS distance
                            FROM symbol_centroids
                            WHERE taxonomy_version = :ver
                            ORDER BY distance ASC
                            LIMIT 1
                            """
                        ),
                        {"ver": taxonomy_version, "vec": vec_str},
                    ).fetchone()
                    if res and res[1] <= threshold:
                        return (str(res[0]), float(res[1]))
                    return None
                except Exception:
                    pass  # fall back to python cosine calculation

            # Python-based cosine calculation fallback
            res = conn.execute(
                text("SELECT canonical_symbol, centroid FROM symbol_centroids WHERE taxonomy_version = :ver"),
                {"ver": taxonomy_version},
            ).fetchall()

            if not res:
                return None

            best_sym = None
            min_dist = float("inf")
            q_norm = np.linalg.norm(query_vector)
            if q_norm == 0:
                return None

            for sym, raw_vec in res:
                c_vec = json.loads(raw_vec) if isinstance(raw_vec, str) else list(raw_vec)
                c_norm = np.linalg.norm(c_vec)
                if c_norm == 0:
                    continue
                # Cosine distance: 1 - cosine_similarity
                cos_sim = float(np.dot(query_vector, c_vec) / (q_norm * c_norm))
                dist = 1.0 - cos_sim
                if dist < min_dist:
                    min_dist = dist
                    best_sym = sym

            if best_sym is not None and min_dist <= threshold:
                return (best_sym, min_dist)
            return None

    # -------------------------------------------------------------------------
    # Violation Recording (PRD §29)
    # -------------------------------------------------------------------------

    def insert_violation(self, violation: ViolationRecord) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        expl_data = (
            violation.explanation.to_dict()
            if hasattr(violation.explanation, "to_dict")
            else (
                violation.explanation.model_dump(mode="json")
                if hasattr(violation.explanation, "model_dump")
                else violation.explanation
            )
        )
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO violations (
                        violation_id, trace_id, event_id, agent_id, role,
                        classification, model_id, policy_id, explanation, created_at
                    ) VALUES (
                        :v_id, :t_id, :e_id, :a_id, :role,
                        :classif, :m_id, :p_id, :expl, :created_at
                    )
                    """
                ),
                {
                    "v_id": str(violation.violation_id),
                    "t_id": str(violation.trace_id),
                    "e_id": str(violation.event_id),
                    "a_id": violation.agent_id,
                    "role": violation.role,
                    "classif": json.dumps(violation.classification),
                    "m_id": str(violation.model_id) if violation.model_id else None,
                    "p_id": str(violation.policy_id) if violation.policy_id else None,
                    "expl": json.dumps(expl_data),
                    "created_at": now,
                },
            )
