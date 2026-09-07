<a id="readme-top"></a>

<!-- PROJECT SHIELDS -->
<div align="center">

[![CI Quality Gates](https://github.com/Atharva-Mendhulkar/TRACE/actions/workflows/ci.yml/badge.svg)](https://github.com/Atharva-Mendhulkar/TRACE/actions/workflows/ci.yml)
[![Tests Passing](https://img.shields.io/badge/Tests-58%2F58%20Passing%20(100%25)-success?style=flat-square&logo=pytest)](trace/tests/)
[![Python Matrix](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Verification Latency](https://img.shields.io/badge/Latency%20p99-%3C0.03ms%20(budget%20%3C5ms)-indigo?style=flat-square)](#rq3-verification-latency)
[![Framework Adapters](https://img.shields.io/badge/Framework%20Adapters-9%20Active-brightgreen?style=flat-square)](#all-9-framework--benchmark-adapters)
[![Docker & Compose](https://img.shields.io/badge/Docker-Postgres%20%7C%20Redis%20%7C%20API-blue?style=flat-square&logo=docker)](docker-compose.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

</div>

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <pre>
  ╱$$                                           
 │ $$                                           
╱$$$$$$    ╱$$$$$$  ╱$$$$$$   ╱$$$$$$$  ╱$$$$$$ 
│_  $$_╱   ╱$$__  $$│____  $$ ╱$$_____╱ ╱$$__  $$
  │ $$    │ $$  ╲__╱ ╱$$$$$$$│ $$      │ $$$$$$$$
  │ $$ ╱$$│ $$      ╱$$__  $$│ $$      │ $$_____╱
  │  $$$$╱│ $$     │  $$$$$$$│  $$$$$$$│  $$$$$$$
   ╲___╱  │__╱      ╲_______╱ ╲_______╱ ╲_______╱
  </pre>

  <h1 align="center">TRACE</h1>

  <p align="center">
    <strong>Trace-based Runtime Automata for Compliance and Enforcement</strong>
    <br />
    Formal Behavioral Inference &middot; Temporal Policy Verification &middot; Heterogeneous AI Agent Safety
    <br />
    <br />
    <a href="#architecture-overview"><strong>Explore Architecture »</strong></a>
    &middot;
    <a href="#quickstart"><strong>Quickstart Guide »</strong></a>
    &middot;
    <a href="#cli-reference--mock-data-test-suite"><strong>CLI & Mock Data »</strong></a>
    &middot;
    <a href="#empirical-benchmark-findings-rq1rq6"><strong>Empirical Benchmarks (RQ1–RQ6) »</strong></a>
    &middot;
    <a href="https://github.com/Atharva-Mendhulkar/TRACE/issues">Report an Issue</a>
  </p>
</div>

---

**TRACE** is an open-source, research-grade system for inferring, verifying, and monitoring the behavioral protocols of heterogeneous AI agent systems in real time. 

TRACE observes stochastic, positive-only agent execution logs across 9 industry frameworks & benchmark ecosystems, extracts formal **Probabilistic Deterministic Finite Automata (PDFA)**, and performs sub-millisecond streaming runtime verification against learned models and declarative safety policies via **dual-control product-automaton composition**.

---

## Architecture Overview

```
                      [ Heterogeneous Agent Frameworks & Benchmarks ]
     MCP · LangGraph · CrewAI · OpenAI Agents · Semantic Kernel · Google ADK · AutoGen · SWE-bench · OSWorld
                                    │
                                    ▼
                      [ Framework Adapter Layer (9/9) ]
                 Normalizes logs to Canonical Event Schema (CES v1.0)
                 Performs strict secret redaction (keys, auth, tokens)
                                    │
                                    ▼
                      [ Streaming Ingestion Pipeline ]
                     Deduplication · Dead-Letter Queue (DLQ)
                                    │
                  ┌─────────────────┴─────────────────┐
                  ▼                                   ▼
        [ Relational / Vector Store ]       [ In-Memory / Redis Stream ]
         PostgreSQL 16 + pgvector            XREADGROUP Consumer Pool
         Cosine Distance HNSW Index          O(1) Verification State Cache
                  │                                   │
                  ▼                                   ▼
        [ Protocol Inference Engine ]       [ Product Automaton Verifier ]
         PTA · ALERGIA/MDI · EDSM            Synchronous Dual Control State
         Hierarchical Folding (§16)          q = (q_learned, q_policy)
         Semantic Clustering (§12)           p99 Latency: 0.0206 ms (< 5ms)
                  │                                   │
                  └─────────────────┬─────────────────┘
                                    ▼
       [ Explainability & HITL ] ───────► [ Web Console & Telemetry ]
        Counterexample Suffixes            Interactive Graph Visualizer
        Triage (Approve / Reject)          FastAPI REST API · Prometheus
```

---

## Key Capabilities

### 1. Canonical Event Schema (CES v1.0) & Adapter Registry
- Normalizes heterogeneous execution traces into an immutable, JSON-Schema draft 2020-12 validated representation.
- Built-in regex and entropy-based secret redaction (`api_key`, `authorization`, bearer tokens, private keys) before parameter schema hashing.
- Complete contract test suites and golden fixtures for **all 7 framework adapters**:
  1. **Model Context Protocol (MCP)** (`stdio`, `sse`)
  2. **LangGraph** (StateGraph nodes, conditional edges)
  3. **CrewAI** (Hierarchical / Sequential crew processes)
  4. **OpenAI Agents SDK** (Assistant API tool runs)
  5. **Semantic Kernel** (Kernel functions and connectors)
  6. **Google Agent Development Kit (ADK)** (GenAI execution spans)
  7. **AutoGen** (ConversableAgent turn events)

### 2. Protocol Inference Engine
- **Prefix Tree Acceptor (PTA)** construction from positive execution traces.
- Native, pure-Python state-merging learner implementing **ALERGIA / MDI** statistical compatibility tests via Hoeffding bounds and **EDSM** (Evidence-Driven State Merging).
- Subprocess integration wrapper for the **FlexFringe** C++ automata learning engine.
- Length-normalized mean per-event Negative Log-Likelihood (NLL) and formal validation checks.

### 3. Hierarchical Delegation Folding (§16)
- Models multi-agent systems with explicit `delegate(role)` boundary symbols.
- Isolates child sub-traces into role-specific training corpora (`get_role_corpus`).
- Enforces a strict `MAX_DELEGATION_DEPTH = 8` recursion ceiling.
- Detects and flags orphan child spans and missing child traces.

### 4. Semantic Action Clustering (§12)
- Character $n$-gram embedding model producing deterministic vectors.
- Agglomerative hierarchical clustering with cosine distance thresholding ($\tau$).
- Nearest-centroid taxonomy mapping reducing raw action space complexity by $> 70\%$.

### 5. Policy DSL & Bounded Counter Compiler
- Declarative domain-specific language for authoring safety invariants:
  - `REQUIRE <action_a> BEFORE <action_b> [WITHIN k EVENTS]`
  - `FORBID SEQUENCE <action_1>, <action_2>, ...`
  - `LIMIT <action> TO k PER TRACE`
- Compiles rules into minimal DFAs using bounded counter product construction with static satisfiability and reachability validation.

### 6. Dual-Control Product Automaton & Sub-Millisecond Verifier
- Jointly tracks product control states $(q_{\text{learned}}, q_{\text{policy}})$ alongside NLL anomaly scores.
- Non-collapsing, three-way deviation classification:
  - `STRUCTURAL_ANOMALY`: Transition unseen from current state in training corpus.
  - `STATISTICAL_ANOMALY`: Transition valid, but transition probability $P(q, \sigma) < \varepsilon$.
  - `POLICY_VIOLATION`: Transition violates an explicit safety rule.
- Verification latency budget strictly $< 5.0\text{ ms}$; measured p99 is **$0.0206\text{ ms}$** ($240\times$ faster than requirement).

### 7. Explainability & Counterexamples
- Computes shortest offending suffixes and expected-vs-observed symbol diffs.
- Resolves internal state IDs to human-readable action names and rule citations.

### 8. Behavioral Drift Detection
- Rolling window conformance buffer with two-sample Kolmogorov-Smirnov (KS) tests and tail-quantile (95th percentile) CUSUM monitoring to detect distribution shifts and trigger out-of-cycle relearning.

### 9. Production Persistence & Session State Cache
- **PostgreSQL 16 + pgvector**: Full relational schema with `VECTOR(768)` embeddings and HNSW cosine distance indexing (`vector_cosine_ops`).
- **Redis 7.2 Session Cache**: $O(1)$ stateful incremental verification tracking per active trace session.

### 10. Human-in-the-Loop (HITL) Feedback & Model Promotion
- Review triage console for runtime violations: **Approve (Valid Novelty)**, **Reject (Confirmed Anomaly)**, **Override Transition**.
- Formal candidate lifecycle: `CANDIDATE` $\rightarrow$ `SHADOWED` $\rightarrow$ `ACTIVE` $\rightarrow$ `SUPERSEDED`.

### 11. Interactive Dark-Mode Web Dashboard Console
- Served directly at `http://localhost:8000/dashboard`.
- Real-time SVG state machine graph visualizer with active state traversal.
- Live streaming event injection sandbox with preset anomaly flows.
- HITL triage console for operator review.
- Empirical benchmark KPI cards (RQ1–RQ6) and Prometheus telemetry stream.

---

## Empirical Benchmark Findings (RQ1–RQ6)

TRACE includes a rigorous benchmark evaluation harness (`trace/benchmarks/evaluator.py`) validating the 6 research questions outlined in the PRD & PAD:

| Research Question | Metric Evaluated | TRACE Measured Result | Target Specification |
|---|---|---|---|
| **RQ1: Sample Complexity** | PDFA convergence on held-out NLL | **Converged at $N=50$ traces** | $\le 100$ traces |
| **RQ2: Automata Recovery** | Precision / Recall / F1 score | **$\text{F}_1 = 0.9615$** (P: 0.9259, R: 1.0000) | $\text{F}_1 \ge 0.90$ |
| **RQ3: Verification Latency** | Single-event verification latency | **$\mathbf{p99 = 0.0206\text{ ms}}$** (p50: 0.015ms) | $\le 5.0\text{ ms}$ ($240\times$ faster) |
| **RQ4: Policy Enforcement** | Safety violation detection accuracy | **$100.0\%$ Accuracy** (0 false negatives) | $100\%$ detection |
| **RQ5: Drift Detection** | Two-sample KS test on shifted traces | **$\text{KS} = 0.9800$, Triggered** | Flag distribution shift |
| **RQ6: Semantic Abstraction**| State space reduction ratio | **$70.83\%$ Reduction** ($24 \rightarrow 7$ states) | $\ge 50\%$ reduction |

---

## Quickstart

### Prerequisites
- Python 3.11, 3.12, 3.13, or 3.14
- Docker & Docker Compose (optional, for multi-service stack)

### 1. Local Installation
```bash
# Clone repository
git clone https://github.com/Atharva-Mendhulkar/TRACE.git
cd TRACE

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install TRACE in editable mode with all dependencies
pip install -e .
pip install pytest pytest-asyncio httpx
```

### 2. Run Test Suite
Run all 45 unit, contract, integration, and benchmark tests:
```bash
pytest trace/tests/ -v
```

### 3. Run Empirical Benchmarks
```bash
trace benchmark run --rq all
```

### 4. Launch TRACE Web Console & API Server
```bash
trace api start --host 0.0.0.0 --port 8000
```
Open **`http://localhost:8000/dashboard`** in your browser to explore the interactive visualizer, streaming verification sandbox, and HITL triage console.

---

## Docker Compose Deployment

To deploy TRACE with PostgreSQL + pgvector, Redis, FastAPI Gateway, and the Streaming Verification Worker:

```bash
docker compose up --build -d
```

### Services Included:
- **`postgres`**: PostgreSQL 16 with `pgvector` extension enabled on port `5432`.
- **`redis`**: Redis 7.2 on port `6379` for stream consumers and session state.
- **`api`**: FastAPI Gateway Server on port `8000` (serving `/dashboard`, `/metrics`, `/v1/events`, `/v1/verify`).
- **`verifier`**: High-throughput Redis Stream verification worker.
- **`learner`**: Background protocol learning worker.

---

## CLI Reference & Mock Data Test Suite

TRACE provides an interactive, Posit/R-`cli`-inspired semantic command-line interface featuring colored banners, styled alert badges (`✔`, `ℹ`, `▲`, `✖`), formatted Unicode tables, and inspection views.

### Mock Dataset (`mock_data/`)

The repository includes a self-contained mock dataset in [`mock_data/`](mock_data/) to test and verify every CLI capability:

| File | Format / Framework | Description |
|---|---|---|
| [`mock_data/events.json`](mock_data/events.json) | MCP JSON-RPC 2.0 | Multi-agent execution traces (`research-agent`, `security-agent`) |
| [`mock_data/benchmark_trajectories.json`](mock_data/benchmark_trajectories.json) | SWE-bench Format | Benchmark trajectories (`search_code` $\to$ `read_file` $\to$ `edit_file` $\to$ `run_test` $\to$ `submit`) |
| [`mock_data/compliance_policy.policy`](mock_data/compliance_policy.policy) | TRACE Policy DSL | Declarative temporal safety rules (`REQUIRE`, `FORBID SEQUENCE`, `LIMIT`) |
| [`mock_data/anomalous_events.json`](mock_data/anomalous_events.json) | MCP JSON-RPC 2.0 | Policy violation trace (`scan_network` without prior `auth_check`) |
| [`mock_data/test_cli.sh`](mock_data/test_cli.sh) | Bash Script | **Automated test script executing all 19 CLI operations end-to-end** |

### One-Line Complete CLI Test
Execute the comprehensive end-to-end test suite against the mock dataset:
```bash
bash mock_data/test_cli.sh
```

---

### Step-by-Step CLI Commands

#### 1. Adapter Registry
```bash
# List all 9 registered framework & benchmark adapters
trace adapter list
```

#### 2. Policy DSL Validation & Compilation
```bash
# Parse and compile declarative safety rules to a DFA
trace policy validate mock_data/compliance_policy.policy
```

#### 3. Ingestion into Storage
```bash
# Ingest normal framework events into SQLite/Postgres
trace ingest mock_data/events.json --framework mcp --db trace.sqlite

# Ingest anomalous traces for violation testing
trace ingest mock_data/anomalous_events.json --framework mcp --db trace.sqlite
```

#### 4. Real-World Benchmark Trajectory Ingestion & Auto-Training
```bash
# Ingest SWE-bench software engineering traces and immediately infer a PDFA
trace ingest-benchmark mock_data/benchmark_trajectories.json --dataset swebench --db trace.sqlite --train
```

#### 5. Automata Learning (ALERGIA / MDI / EDSM)
```bash
# Infer PDFA protocol model for research-agent
trace train --agent-id research-agent --engine native-alergia --heuristic alergia --alpha 0.05 --db trace.sqlite

# Infer PDFA protocol model for security-agent
trace train --agent-id security-agent --engine native-alergia --heuristic alergia --alpha 0.05 --db trace.sqlite
```

#### 6. Model Lifecycle & State Machine Inspection
```bash
# List all candidate and active models
trace model list --db trace.sqlite

# Inspect learned state machine transitions, counts, and probabilities (δ, P, n)
trace model inspect <MODEL_UUID> --db trace.sqlite

# Validate stochastic and structural invariants
trace validate --model-id <MODEL_UUID> --db trace.sqlite

# Promote candidate model to active production
trace model promote <MODEL_UUID> --activate --db trace.sqlite
```

#### 7. Trace Trajectory Replay & Streaming Verification
```bash
# Replay stored execution trajectory
trace replay --trace-id trace-research-001 --db trace.sqlite

# Verify conforming trace against active PDFA model (PASSED verdict)
trace verify --trace-id trace-research-001 --db trace.sqlite

# Verify anomalous trace against safety policy (FAILED with violation diff)
trace verify --trace-id trace-violation-001 --policy mock_data/compliance_policy.policy --db trace.sqlite
```

#### 8. Human-in-the-Loop (HITL) Violation Triage
```bash
# Record reviewer audit decision for a runtime violation
trace feedback record \
  --violation-id viol-001 \
  --type approve \
  --reviewer secops-lead \
  --comment "Authorized security audit exception" \
  --db trace.sqlite

# List recorded feedback triage records
trace feedback list --db trace.sqlite
```

#### 9. Empirical Evaluation Benchmarks (RQ1–RQ6)
```bash
# Run single RQ evaluation (e.g. RQ3 Latency Overhead)
trace benchmark run --rq 3

# Run all empirical evaluations (RQ1 through RQ6)
trace benchmark run --rq all
```

---

## REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` or `/dashboard` | `GET` | Interactive Dark-Mode Web Console (SVG Graph, Sandbox, HITL) |
| `/health` | `GET` | System health check and database status |
| `/metrics` | `GET` | Prometheus telemetry metric stream |
| `/v1/demo/seed` | `POST` | Seed demo models, policies, and violations for instant sandbox testing |
| `/v1/events` | `POST` | Ingest a batch of Canonical Event Schema (CES v1.0) records |
| `/v1/verify` | `POST` | Verify streaming execution event against active PDFA and safety policy |
| `/v1/models` | `GET` | List all learned models and their lifecycle status |
| `/v1/models/{model_id}` | `GET` | Inspect state machine definition, alphabet, and transitions |
| `/v1/models/{model_id}/promote` | `POST` | Promote candidate model to `ACTIVE` |
| `/v1/feedback` | `POST` | Submit HITL review decision (`approve`, `reject`, `override`) |
| `/v1/feedback` | `GET` | List recorded human review decisions |

---

## Repository Structure

```
TRACE/
├── .github/
│   └── workflows/ci.yml       # GitHub Actions multi-python test & docker matrix
├── mock_data/                 # Self-contained CLI test dataset & automated bash runner
│   ├── events.json            # Multi-agent MCP framework trace logs
│   ├── benchmark_trajectories.json # SWE-bench real-world benchmark trajectories
│   ├── compliance_policy.policy # Declarative safety policy (REQUIRE, FORBID, LIMIT)
│   ├── anomalous_events.json  # Policy violation traces for verifier triage
│   └── test_cli.sh            # Complete 19-step automated CLI test script
├── trace/
│   ├── schema/                # CES v1.0 JSON Schema & Pydantic models
│   ├── adapters/              # 9 Framework & benchmark adapters (MCP, LangGraph, SWE-bench, etc.)
│   ├── ingestion/             # Ingestion pipeline, dead-letter routing, dedup
│   ├── abstraction/           # Agglomerative clustering & taxonomy mapping
│   ├── corpus/                # Trace repository, windowing, completeness checks
│   ├── inference/             # PTA builder, native ALERGIA/EDSM learner, FlexFringe
│   ├── models/                # PDFA formal model, repository, lifecycle management
│   ├── policy/                # Policy DSL parser, DFA compiler, product automaton
│   ├── verification/          # Sub-ms runtime verifier, session cache, replay engine
│   ├── explainability/        # Counterexample generation and human-readable diffs
│   ├── drift/                 # Rolling window KS test and tail-quantile CUSUM detector
│   ├── storage/               # PostgreSQL + pgvector schema and store implementation
│   ├── feedback/              # HITL review triage engine and candidate promotion
│   ├── api/                   # FastAPI gateway, Prometheus metrics, Web Dashboard
│   ├── streaming/             # Redis Stream and async queue consumers / workers
│   ├── benchmarks/            # RQ1–RQ6 benchmark evaluation suite and generator
│   ├── cli/                   # Semantic CLI interface & Posit/R-cli UI engine
│   └── tests/                 # 58 unit, contract, integration, and benchmark tests
├── Dockerfile                 # Multi-stage Python production container
├── docker-compose.yml         # Multi-container stack (Postgres+pgvector, Redis, API, Worker)
├── pyproject.toml             # Python build configuration and dependencies
└── LICENSE                    # MIT License
```

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

<p align="right">(<a href="#readme-top">back to top</a>)</p>
