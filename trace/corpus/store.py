"""
Durable Trace Store & Corpus Manager (PRD §13, §29).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from trace.schema.models import CESRecord, EventAttributes, ErrorInfo, ProvenanceInfo


class TraceStore:
    """SQLite-backed implementation of M3 Trace Store conforming to TraceRepository."""

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self.db_path = str(db_path)
        self._shared_conn = sqlite3.connect(":memory:") if self.db_path == ":memory:" else None
        if self._shared_conn:
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._shared_conn:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                parent_span_id TEXT,
                session_id TEXT,
                agent_id TEXT NOT NULL,
                role TEXT,
                depth INTEGER NOT NULL DEFAULT 0,
                event_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                raw_symbol TEXT NOT NULL,
                framework TEXT NOT NULL,
                framework_schema_version TEXT NOT NULL,
                adapter_version TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                param_schema_hash TEXT NOT NULL,
                taxonomy_version INTEGER NOT NULL DEFAULT 1,
                error_class TEXT,
                retryable INTEGER,
                timestamp TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_trace 
                ON events (trace_id, span_id, sequence_no);
            CREATE INDEX IF NOT EXISTS idx_events_agent_time 
                ON events (agent_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_parent_span 
                ON events (parent_span_id);
            CREATE INDEX IF NOT EXISTS idx_events_symbol 
                ON events (symbol, taxonomy_version);
            """)

    def write_event(self, record: CESRecord) -> bool:
        """Idempotently insert an event. Returns True if inserted, False if duplicate (PRD §10.5)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM events WHERE event_id = ?", (record.event_id,))
            if cur.fetchone():
                return False

            error_class = record.error.error_class if record.error else None
            retryable = int(record.error.retryable) if record.error else None

            cur.execute("""
                INSERT INTO events (
                    event_id, trace_id, span_id, parent_span_id, session_id,
                    agent_id, role, depth, event_type, symbol, raw_symbol,
                    framework, framework_schema_version, adapter_version,
                    sequence_no, status, param_schema_hash, taxonomy_version,
                    error_class, retryable, timestamp, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.event_id, record.trace_id, record.span_id, record.parent_span_id,
                record.session_id, record.agent_id, record.role, record.depth,
                record.event_type, record.symbol, record.raw_symbol,
                record.framework, record.framework_schema_version, record.adapter_version,
                record.sequence_no, record.status, record.attributes.param_schema_hash,
                1, error_class, retryable, record.timestamp,
                record.provenance.ingested_at or record.timestamp
            ))
            conn.commit()
            return True

    def write_events(self, records: List[CESRecord]) -> int:
        """Insert batch of records. Returns count of newly inserted records."""
        inserted = 0
        for r in records:
            if self.write_event(r):
                inserted += 1
        return inserted

    def get_trace(self, trace_id: str, span_id: Optional[str] = None) -> List[CESRecord]:
        """Reconstruct ordered event sequence per (trace_id, span_id) using sequence_no (PRD §10.4)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            if span_id:
                cur.execute(
                    "SELECT * FROM events WHERE trace_id = ? AND span_id = ? ORDER BY sequence_no ASC",
                    (trace_id, span_id)
                )
            else:
                cur.execute(
                    "SELECT * FROM events WHERE trace_id = ? ORDER BY depth ASC, sequence_no ASC",
                    (trace_id,)
                )
            rows = cur.fetchall()
            return [self._row_to_record(row) for row in rows]

    def is_trace_complete(self, trace_id: str) -> bool:
        """Check if a trace contains a terminate event at depth 0 (PRD §13.4)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM events WHERE trace_id = ? AND depth = 0 AND event_type = 'terminate'",
                (trace_id,)
            )
            return cur.fetchone() is not None

    def get_all_trace_ids(self, agent_id: Optional[str] = None) -> List[str]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            if agent_id:
                cur.execute("SELECT DISTINCT trace_id FROM events WHERE agent_id = ?", (agent_id,))
            else:
                cur.execute("SELECT DISTINCT trace_id FROM events")
            return [row[0] for row in cur.fetchall()]

    def get_corpus(
        self,
        agent_id: str,
        include_truncated: bool = False,
        taxonomy_version: int = 1,
    ) -> List[List[str]]:
        """Return list of symbolic traces over alphabet Sigma (PRD §13.2, §13.4)."""
        trace_ids = self.get_all_trace_ids(agent_id=agent_id)
        corpus: List[List[str]] = []

        for t_id in trace_ids:
            if not include_truncated and not self.is_trace_complete(t_id):
                continue
            # Get top-level span (depth 0)
            events = self.get_trace(t_id)
            depth_0_events = [e for e in events if e.depth == 0]
            if depth_0_events:
                symbolic_trace = [e.symbol for e in depth_0_events]
                corpus.append(symbolic_trace)

        return corpus

    def get_role_corpus(
        self,
        role: str,
        include_truncated: bool = False,
        taxonomy_version: int = 1,
    ) -> List[List[str]]:
        """Return list of symbolic traces for a delegated role across all traces (PRD §16.2)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT trace_id, span_id FROM events WHERE role = ? AND (depth > 0 OR parent_span_id IS NOT NULL)",
                (role,)
            )
            spans = cur.fetchall()

        corpus: List[List[str]] = []
        for row in spans:
            t_id, s_id = row[0], row[1]
            events = self.get_trace(t_id, span_id=s_id)
            if not events:
                continue
            if not include_truncated:
                has_end = any(
                    e.event_type in ("terminate", "return") or e.symbol in ("return", "terminate")
                    for e in events
                )
                if not has_end:
                    continue
            symbolic_trace = [e.symbol for e in events]
            if symbolic_trace:
                corpus.append(symbolic_trace)
        return corpus

    def get_delegated_traces(self, parent_span_id: str) -> List[CESRecord]:
        """Fetch child events belonging to a specific parent delegation span (PRD §16.2)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM events WHERE parent_span_id = ? ORDER BY sequence_no ASC",
                (parent_span_id,)
            )
            return [self._row_to_record(row) for row in cur.fetchall()]

    def _row_to_record(self, row: sqlite3.Row) -> CESRecord:
        error = None
        if row["error_class"]:
            error = ErrorInfo(
                error_class=row["error_class"],
                retryable=bool(row["retryable"]),
            )
        return CESRecord(
            schema_version="1.0",
            event_id=row["event_id"],
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            role=row["role"],
            depth=row["depth"],
            event_type=row["event_type"],
            symbol=row["symbol"],
            raw_symbol=row["raw_symbol"],
            attributes=EventAttributes(
                param_schema_hash=row["param_schema_hash"],
                status=row["status"],
            ),
            error=error,
            timestamp=row["timestamp"],
            framework=row["framework"],
            framework_schema_version=row["framework_schema_version"],
            adapter_version=row["adapter_version"],
            sequence_no=row["sequence_no"],
            status=row["status"],
            provenance=ProvenanceInfo(
                timestamp_source="framework",
                ingested_at=row["ingested_at"],
            ),
        )


# Alias for explicit repository interface conformance
SQLiteTraceRepository = TraceStore

