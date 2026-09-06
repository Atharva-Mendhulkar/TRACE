# TRACE: Trace-based Runtime Automata for Compliance and Enforcement

TRACE is a research-grade system for inferring, verifying, and monitoring the behavioral protocols of AI agent systems across heterogeneous execution frameworks (MCP, LangGraph, CrewAI, OpenAI Agents SDK, Semantic Kernel, Google ADK, AutoGen).

TRACE models normal agent behavior from stochastic, positive-only execution traces using **Probabilistic Deterministic Finite Automata (PDFA)** and performs streaming and replay conformance checking against learned models and explicit safety policies via **product-automaton composition**.

---

## Key Capabilities

- **Canonical Event Schema (CES v1.0)**: Normalizes heterogeneous framework logs into a formal, JSON-Schema validated representation with secret redaction and idempotency.
- **Framework Adapters**: Production-ready adapters for **Model Context Protocol (MCP)** and **LangGraph**, complete with contract test suites.
- **Protocol Inference Engine**:
  - **Prefix Tree Acceptor (PTA)** construction from positive trace corpora.
  - Native pure-Python state-merging learner implementing **EDSM** (Evidence-Driven State Merging) and **ALERGIA / MDI** statistical compatibility tests via Hoeffding bounds.
  - Subprocess integration wrapper for the **FlexFringe** C++ state-merging toolkit.
- **PDFA Formal Representation**: Deterministic partial state transition function $\delta(q, \sigma)$, maximum-likelihood transition probabilities $P(q, \sigma)$, operational termination semantics, length-normalized mean per-event Negative Log-Likelihood (NLL), and model validation checks.
- **Policy DSL & Compiler**: Author declarative safety rules (`REQUIRE ... BEFORE ... [WITHIN k EVENTS]`, `FORBID SEQUENCE [...]`, `LIMIT ...`) compiled into DFAs using bounded-counter product constructions with static emptiness/universality checks.
- **Product Automaton Runtime Verifier**: Jointly tracks product control states $(q_{learned}, q_{policy})$ with a parallel NLL scalar to classify deviations into three distinct, non-collapsed categories:
  - **Structural Anomaly**: transition unseen from current state in training.
  - **Statistical Anomaly**: transition seen but $P(q, \sigma) < \varepsilon$.
  - **Policy Violation**: transition violates an explicit safety rule.
- **Explainability & Counterexamples**: Emits shortest offending suffixes, expected-vs-observed symbol diffs, and resolves internal state IDs back to human-readable action names.
- **Behavioral Drift Detection**: Rolling window buffer with two-sample Kolmogorov-Smirnov (KS) tests and tail-quantile (95th percentile) CUSUM monitoring to detect distribution shifts and trigger out-of-cycle relearning.
- **Synthetic Ground-Truth Benchmarks (RQ2)**: Evaluates PDFA recovery across increasing corpus sizes under controlled noise injection.

---

## Directory Structure

```
TRACE/
├── trace/
│   ├── schema/           # CES v1.0 JSON Schema and Pydantic models
│   ├── adapters/         # Framework adapters (MCP, LangGraph, registry)
│   ├── ingestion/        # Ingestion pipeline, dead-letter routing, dedup
│   ├── abstraction/      # Rule-based & embedding-based semantic abstraction
│   ├── corpus/           # SQLite trace store, windowing, completeness checks
│   ├── inference/        # PTA builder, native EDSM/ALERGIA learner, FlexFringe wrapper
│   ├── models/           # PDFA formal model, repository, lifecycle states
│   ├── policy/           # Policy DSL parser, DFA compiler, product automaton
│   ├── verification/     # Runtime verifier, state tracking, replay engine
│   ├── explainability/   # Counterexample generation and human-readable diffs
│   ├── drift/            # Rolling window KS test and tail-quantile drift detector
│   ├── cli/              # Command-line interface
│   └── tests/            # Unit, contract, and property-based test suites
├── benchmarks/           # Synthetic ground-truth generator and RQ benchmarks
├── pyproject.toml        # Project configuration and packaging
├── prd.md                # Technical Product Requirements Document
├── pad.md                # Principal Architect Document
└── disclosure.md         # Invention Disclosure & Prior-Art Analysis Report
```

---

## Installation & Setup

### Requirements
- Python 3.10+
- Linux / macOS / WSL

### Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Running Tests
Execute the comprehensive test suite (18 unit, contract, and integration tests):
```bash
pytest trace/tests -v
```

---

## CLI Usage Guide

### 1. Inspect Registered Framework Adapters
```bash
trace adapter list
```

### 2. Ingest Execution Traces
Ingest MCP or LangGraph logs:
```bash
trace ingest trace/tests/fixtures/mcp_normal.json --db trace.sqlite
trace ingest trace/tests/fixtures/langgraph_normal.json --db trace.sqlite
```

### 3. Train a Behavioral Protocol (PDFA)
Learn a PDFA from complete traces using ALERGIA statistical state merging:
```bash
trace train --agent-id mcp-researcher --heuristic alergia --alpha 0.05 --db trace.sqlite
```

### 4. Inspect Inferred Automata
List and inspect learned state machines and transition probabilities:
```bash
trace model list --db trace.sqlite
trace model inspect <MODEL_UUID> --db trace.sqlite
```

### 5. Validate Policy DSL Files
Compile a declarative safety policy and perform static satisfiability checks:
```bash
trace policy validate trace/tests/fixtures/sample.policy
```

### 6. Verify Traces & Generate Explanations
Verify an execution trace against the learned PDFA and safety policy:
```bash
trace verify --trace-id <TRACE_UUID> --policy trace/tests/fixtures/sample.policy --db trace.sqlite
```

### 7. Replay Stored Traces
Step through historical execution traces:
```bash
trace replay --trace-id <TRACE_UUID> --db trace.sqlite
```

---

## Research Benchmarks (RQ2)

Run the synthetic ground-truth PDFA recovery experiment (evaluating held-out log-likelihood and coverage across increasing training sample sizes $N \in \{20, 50, 100, 200\}$):
```bash
python3 -m benchmarks.synthetic_generator
```

---

## Architectural Decisions & References

- **PRD**: See [prd.md](file:///home/topfloorboss/Desktop/TRACE/prd.md) for full architectural requirements, storage schemas, and open questions.
- **PAD**: See [pad.md](file:///home/topfloorboss/Desktop/TRACE/pad.md) for research narrative, theoretical mapping, and evaluation methodology.
- **Prior Art**: See [disclosure.md](file:///home/topfloorboss/Desktop/TRACE/disclosure.md) for patent and literature analysis (TraceAegis, FlexFringe, LogLens).
