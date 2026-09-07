-- ============================================================================
-- TRACE PostgreSQL + pgvector Storage Schema (PRD §29)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- Canonical Event Schema (CES) Event Ingestion Store
CREATE TABLE IF NOT EXISTS events (
    event_id            UUID PRIMARY KEY,
    trace_id            UUID NOT NULL,
    span_id             UUID NOT NULL,
    parent_span_id      UUID,
    agent_id            TEXT NOT NULL,
    role                TEXT,
    depth               INT NOT NULL DEFAULT 0,
    framework           TEXT NOT NULL,
    framework_schema_version TEXT NOT NULL,
    adapter_version     TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    raw_symbol          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    sequence_no         BIGINT NOT NULL,
    status              TEXT NOT NULL,
    param_schema_hash   TEXT NOT NULL,
    taxonomy_version    INT NOT NULL DEFAULT 1,
    timestamp           TIMESTAMPTZ NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload             JSONB
);

CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id, span_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_events_agent_time ON events (agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_parent_span ON events (parent_span_id);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON events (symbol, taxonomy_version);
CREATE INDEX IF NOT EXISTS idx_events_framework ON events (framework);
CREATE INDEX IF NOT EXISTS idx_events_role ON events (role);

-- Symbol Taxonomy & Centroids (Semantic Abstraction Engine M2)
CREATE TABLE IF NOT EXISTS symbol_taxonomies (
    taxonomy_version    INT PRIMARY KEY,
    embedding_model_version TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbol_centroids (
    taxonomy_version    INT REFERENCES symbol_taxonomies(taxonomy_version),
    canonical_symbol    TEXT NOT NULL,
    centroid            VECTOR(768),   -- pgvector; dimension per embedding model (§12.3)
    PRIMARY KEY (taxonomy_version, canonical_symbol)
);

CREATE INDEX IF NOT EXISTS idx_centroids_hnsw ON symbol_centroids USING hnsw (centroid vector_cosine_ops);

-- Models (Learned PDFA Automata)
CREATE TABLE IF NOT EXISTS models (
    model_id            UUID PRIMARY KEY,
    model_version       INT NOT NULL,
    agent_id            TEXT NOT NULL,
    role                TEXT,
    taxonomy_version    INT NOT NULL DEFAULT 1,
    training_corpus_hash TEXT NOT NULL,
    training_window_start TIMESTAMPTZ NOT NULL DEFAULT now(),
    training_window_end   TIMESTAMPTZ NOT NULL DEFAULT now(),
    learner_config      JSONB NOT NULL,
    evaluation_metrics  JSONB,
    status              TEXT NOT NULL CHECK (status IN
        ('TRAINING','VALIDATION','CANDIDATE','PROMOTED','ACTIVE','SUPERSEDED','ARCHIVED')),
    creator             TEXT NOT NULL,
    trained_at          TIMESTAMPTZ DEFAULT now(),
    validated_at        TIMESTAMPTZ,
    promoted_at         TIMESTAMPTZ,
    activated_at        TIMESTAMPTZ,
    superseded_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_models_agent_status ON models (agent_id, status);
CREATE INDEX IF NOT EXISTS idx_models_role_status ON models (role, status);

CREATE TABLE IF NOT EXISTS automaton_states (
    model_id            UUID REFERENCES models(model_id) ON DELETE CASCADE,
    state_id            TEXT NOT NULL,
    is_final            BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (model_id, state_id)
);

CREATE TABLE IF NOT EXISTS automaton_transitions (
    model_id            UUID REFERENCES models(model_id) ON DELETE CASCADE,
    from_state          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    to_state            TEXT NOT NULL,
    frequency           BIGINT NOT NULL DEFAULT 1,
    probability         DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    low_confidence      BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (model_id, from_state, symbol)
);

CREATE INDEX IF NOT EXISTS idx_transitions_model ON automaton_transitions (model_id, from_state);

-- Policies (Compiled Deterministic Finite Automata)
CREATE TABLE IF NOT EXISTS policies (
    policy_id           UUID PRIMARY KEY,
    name                TEXT NOT NULL,
    policy_version      INT NOT NULL,
    source              TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    author              TEXT NOT NULL,
    compiled_dfa        JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_name_version ON policies (name, policy_version);

-- Violations (Runtime Enforcer)
CREATE TABLE IF NOT EXISTS violations (
    violation_id        UUID PRIMARY KEY,
    trace_id            UUID NOT NULL,
    event_id            UUID NOT NULL,
    agent_id            TEXT NOT NULL,
    role                TEXT,
    classification      TEXT[] NOT NULL,
    model_id            UUID REFERENCES models(model_id),
    policy_id           UUID REFERENCES policies(policy_id),
    explanation         JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_violations_agent_time ON violations (agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_violations_trace ON violations (trace_id);

-- Concept Drift Events
CREATE TABLE IF NOT EXISTS drift_events (
    drift_id            UUID PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    model_id            UUID REFERENCES models(model_id),
    test_used           TEXT NOT NULL,
    statistic           DOUBLE PRECISION NOT NULL,
    p_value             DOUBLE PRECISION,
    severity            TEXT NOT NULL,
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    relearn_triggered   BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drift_agent_time ON drift_events (agent_id, created_at);

-- Human-in-the-Loop Feedback (M10, §23.4)
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id         UUID PRIMARY KEY,
    violation_id        UUID REFERENCES violations(violation_id) ON DELETE CASCADE,
    feedback_type       TEXT NOT NULL,
    reviewer            TEXT NOT NULL,
    comment             TEXT,
    applied             BOOLEAN NOT NULL DEFAULT false,
    applied_in_model_id UUID REFERENCES models(model_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_violation ON feedback (violation_id);

-- Live Verification State (Stream Hot Path, PRD §17, §29)
CREATE TABLE IF NOT EXISTS verification_state (
    trace_id            UUID NOT NULL,
    span_id             UUID NOT NULL,
    model_id            UUID REFERENCES models(model_id),
    policy_id           UUID REFERENCES policies(policy_id),
    current_learned_state TEXT NOT NULL,
    current_policy_state TEXT NOT NULL,
    running_mean_nll    DOUBLE PRECISION NOT NULL DEFAULT 0,
    running_events_count INT NOT NULL DEFAULT 0,
    last_event_time     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, span_id)
);
