# TRACE

### Principal Architect Document

---

## 0. Scoping Statement

This document redesigns TRACE as a research-grade system for inferring, verifying, and monitoring the behavioral protocols of AI agent systems (MCP, LangGraph, CrewAI, OpenAI Agents SDK, Semantic Kernel, Google ADK, AutoGen). It does not propose a new automata-learning algorithm. It adapts and composes existing passive learning algorithms (RPNI, EDSM, ALERGIA/MDI, as implemented in FlexFringe) around a novel preprocessing and verification architecture suited to noisy, stochastic, partially-observed, and recursively-structured execution traces. Claims are scoped as "to the best of our knowledge, this combination has not been evaluated for AI agent traces specifically" — not as a first-of-kind or general solution to AI security.

---

## 1. Research Narrative

### 1.1 Why this problem exists

Agent frameworks now produce execution traces — sequences of tool calls, planning steps, memory reads/writes, retries, sub-agent delegations, and external API interactions — that are rich enough to reason about behaviorally, but are treated today as unstructured logs. Two practices dominate:

- **Ad hoc log review**: humans read traces after an incident.
- **Single-step guardrails**: content filters or policy checks applied to one tool call in isolation (e.g., "is this SQL query safe"), with no notion of the _sequence_ the call sits in.

Neither captures a behavioral question that matters increasingly as agents compose tools and delegate to sub-agents: _is this execution, as a whole, consistent with how this agent normally behaves?_ Answering that requires a model of "normal behavior" — and nobody writes one by hand, because the space of legitimate trajectories through a multi-tool, LLM-planned agent is too large and too fluid to specify manually. TRACE's premise is that this model should be inferred from the traces themselves.

### 1.2 Why existing work is insufficient

- **Runtime monitoring (RV)** frameworks (e.g., RV-Monitor-style tools, stream-based monitors such as those built on MTL/LTL) are effective once a specification exists, but assume a human writes that specification. Agent behavior has no such specification in practice, and hand-writing one for every tool combination is not tractable.
- **Manual formal verification** operates on a design-time model of the system, obtained before execution (e.g., a process algebra or explicit state machine of the intended architecture). Agent behavior is not statically known: an LLM planner chooses actions at runtime, and its choices are influenced by sampling, retrieved context, and model updates. There is no static model to verify against.
- **Process mining** (Alpha-miner, Heuristics Miner, Inductive Miner, and their tool ecosystems) is built for business-process event logs: comparatively low-cardinality alphabets, mostly deterministic control flow, and stable process definitions across the mining window. AI agent traces differ on every one of these axes (see 1.3).
- **Protocol/grammatical inference** (RPNI, EDSM, FlexFringe, LearnLib-style tools) has a strong track record on network and API protocols, but those target systems are largely deterministic communicating state machines with a small, fixed message alphabet and low noise. The learning-theoretic guarantees these algorithms rely on (e.g., identification in the limit from a characteristic sample, for RPNI) were derived for that setting, not for a stochastic, high-cardinality, non-stationary source.

### 1.3 Why AI execution traces differ from classical protocol traces

|Property|Classical protocol trace|AI agent trace|
|---|---|---|
|Determinism|Same input → same message sequence|Same input → distributionally similar but not identical sequences (LLM sampling)|
|Observability|Full message content observable|Internal reasoning (chain-of-thought) usually unlogged; only externally visible actions are|
|Alphabet|Small, fixed, well-defined|Large, framework-specific, semantically overlapping (same intent, different names)|
|Symbol frequency|Each symbol seen often|Long-tailed; many tools seen rarely|
|Stationarity|Fixed protocol implementation|Drifts as prompts/models/tools change|
|Structure|Mostly flat sequences|Recursive: agents delegate to sub-agents, producing nested traces|
|"Errors"|Unexpected message = protocol violation|Retries, timeouts, fallback paths are normal, expected operation|

### 1.4 Why passive automata learning is appropriate

Passive learning constructs a model from example traces alone, without a teacher or hand-written spec — matching the reality that agent traces exist but specifications don't. State-merging algorithms (RPNI/EDSM) and probabilistic variants (ALERGIA/MDI) have established theoretical grounding (polynomial-time learnability from characteristic or stochastic samples under stated assumptions) that TRACE can inherit rather than re-derive. What passive learning does _not_ do out of the box is handle heterogeneous multi-framework alphabets, legitimate stochastic branching, non-stationarity, or recursive structure — which is where TRACE's actual contribution sits: not in the learner, but around it.

### 1.5 The actual research contribution

TRACE's contribution is a **semantic trace-abstraction layer** that projects heterogeneous agent logs into a common alphabet suitable for existing learners; an **architecture** that combines that abstraction with probabilistic passive learning, product-automaton runtime verification, and drift detection tailored to agent-trace noise properties; and an **empirical study** of whether the resulting inferred protocols meaningfully capture normal behavior and detect anomalies/drift in practice. It is, to the best of our knowledge, the first system to apply passive automata learning to multi-framework AI agent execution traces specifically — a narrower and more defensible claim than "first ever runtime verification for AI agents."

---

## 2. Research Questions

1. **RQ1 (Abstraction).** How can heterogeneous agent-framework logs be mapped to a common symbolic alphabet while preserving behaviorally relevant distinctions and discarding incidental noise?
2. **RQ2 (Learnability).** To what extent can passive automata learning recover a stable, meaningful behavioral model from noisy, stochastic agent traces, relative to its known performance on classical protocol traces?
3. **RQ3 (Scalability).** How does inferred-model quality and inference time scale with corpus size, alphabet size, and structural complexity (recursion depth, delegation fan-out)?
4. **RQ4 (Stochastic fidelity).** Does a probabilistic automaton representation improve fidelity to legitimate stochastic variability, versus a plain DFA, without over- or under-fitting?
5. **RQ5 (Drift detection).** Can behavioral drift — a change in underlying model, prompt, or toolset — be detected as a measurable shift in trace-to-automaton conformance, and at what latency/accuracy?
6. **RQ6 (Verification).** Can the inferred automaton be composed with an explicit policy automaton to perform online conformance checking with explainable counterexamples at acceptable runtime overhead?
7. **RQ7 (Explainability).** Do automaton-based explanations of deviation improve a developer's ability to diagnose agent misbehavior compared to raw log inspection?
8. **RQ8 (Generalization).** After abstraction, does a protocol learned on one framework's traces transfer to detect anomalies in a semantically similar agent built on a different framework?

---

## 3. Research Contributions

**Engineering contributions.** A reference implementation; canonical-event-schema adapters for MCP, LangGraph, CrewAI, OpenAI Agents SDK, Semantic Kernel, Google ADK, and AutoGen; a pluggable ingestion→abstraction→learning→verification pipeline; an open-source dashboard. These matter because adoption and reproducibility depend on the tool actually working across the fragmented framework landscape, not on a single closed benchmark.

**Algorithmic contributions.** Adaptation of EDSM/ALERGIA-style state merging with frequency-weighted, statistically-tested merge acceptance suited to stochastic traces; a hierarchical trace-folding scheme that represents recursive agent delegation as composed per-level automata rather than a flat alphabet; a windowed incremental-relearning strategy for non-stationary sources. These matter because they are the specific technical adaptations that make passive learning viable on this data, distinct from applying an off-the-shelf learner unmodified.

**Formal-method contributions.** A formal definition of the trace-abstraction map and a statement of what soundness/completeness properties it can and cannot preserve relative to an assumed ground-truth behavior distribution; a product-automaton formalization of runtime verification against combined learned+explicit policies; a formalization of behavioral drift as a decision problem over automaton distance. These matter because they let later work reason precisely about what the system guarantees versus what it merely tends to do empirically.

**Evaluation contributions.** A benchmark suite spanning synthetic ground-truth traces and real multi-framework agent traces; anomaly-detection and drift-detection metrics; ablations isolating the abstraction layer, the representation choice, and the relearning strategy; baselines against naive process mining, raw-sequence n-gram models, and an LLM-as-judge anomaly detector. These matter because "protocol inference works on agent traces" is an empirical hypothesis, not an assumption, and the field needs a way to falsify it.

---

## 4. Complete Architecture

Twelve modules, in pipeline order with one cross-cutting orchestration module and one cross-cutting feedback module.

**M1 — Trace Ingestion & Adapter Layer**

- _Purpose_: normalize framework-specific execution logs into a raw event stream.
- _Inputs_: MCP JSON-RPC call/response pairs, LangGraph node/edge execution events, CrewAI task events, OpenAI Agents SDK run steps, Semantic Kernel planner traces, Google ADK agent events, AutoGen conversation messages.
- _Outputs_: a stream of raw events tagged with source framework and schema version.
- _Algorithms_: per-framework rule-based parsers; schema-version detection.
- _Complexity_: O(n) in trace length.
- _Interfaces_: pull (batch log import) and push (streaming webhook/callback) modes.
- _Failure modes_: framework API changes silently break a parser (schema drift, not behavioral drift); partial/truncated traces from crashed agents.
- _Extensibility_: adapters are isolated plugins; new frameworks require only a new adapter conforming to the raw-event interface, not pipeline changes.

**M2 — Semantic Trace Abstraction Engine** (detailed in §5)

- _Purpose_: map raw, framework-specific events to a common Canonical Event Schema (CES) symbol.
- _Inputs_: raw event stream from M1.
- _Outputs_: symbolic trace over a shared alphabet Σ.
- _Algorithms_: rule-based canonicalization plus embedding-based clustering for near-duplicate tool names/signatures.
- _Complexity_: clustering is the dominant cost, O(k log k) for k distinct raw symbols using approximate nearest-neighbor indexing.
- _Failure modes_: over-clustering (semantically distinct actions collapsed together, hiding real anomalies) and under-clustering (alphabet explosion defeating learnability); both are measured, not just risked (see 5.5).
- _Extensibility_: clustering model is swappable; taxonomy of action types is versioned separately from the clustering weights.

**M3 — Trace Store / Corpus Manager**

- _Purpose_: durable, queryable storage of symbolic traces with provenance (source framework, timestamp, agent identity, task label if available).
- _Outputs_: windowed corpora for training and evaluation.
- _Failure modes_: corpus imbalance (one task type dominating the training window) biasing the learned model.

**M4 — Protocol Inference Engine** (detailed in §6)

- _Purpose_: learn a probabilistic automaton from a symbolic-trace corpus.
- _Algorithms_: EDSM / ALERGIA-MDI via FlexFringe, wrapped with a custom evidence-plus-frequency merge heuristic.
- _Complexity_: state-merging search is NP-hard in the worst case; EDSM's greedy evidence-driven order gives no polynomial guarantee but is the practical standard.
- _Failure modes_: over-generalization (merging states that were behaviorally distinct) and under-generalization (alphabet/data sparsity preventing merges that should happen).

**M5 — Formal Model Repository**

- _Purpose_: version and store learned automata (PDFA) with metadata (training window, hyperparameters, corpus hash) enabling reproducible comparison across relearning runs.
- _Algorithms_: canonical minimization (Hopcroft-style) before storage, so structurally identical models compare equal regardless of learning-run incidental differences.

**M6 — Runtime Verification / Conformance Checking Engine** (detailed in §8)

- _Purpose_: check a live or completed trace against the learned PDFA and/or explicit policy automata via product construction.
- _Complexity_: O(1) amortized per event for state tracking; O(|Σ|) for full product-transition evaluation.
- _Failure modes_: false positives on legitimate rare-but-valid paths if the probability threshold is mis-tuned.

**M7 — Behavioral Drift Detector**

- _Purpose_: detect when the live trace distribution has shifted from the model's training distribution.
- _Algorithms_: rolling conformance-score monitoring (per-symbol negative log-likelihood under the current PDFA) with a distributional change test (e.g., Kolmogorov–Smirnov) over successive windows.
- _Failure modes_: conflating drift with a single unusual-but-legitimate session; needs enough traces per window to have statistical power.

**M8 — Policy Specification & Product Automaton Builder**

- _Purpose_: compile a small declarative policy language over Σ into an explicit policy DFA, and construct its product with the learned PDFA and the live trace's current state.
- _Failure modes_: policy-author error (a policy that is unsatisfiable or vacuously satisfied); mitigated by static policy-automaton sanity checks (emptiness/universality checks, per §9).

**M9 — Explainability & Counterexample Generator**

- _Purpose_: turn a product-automaton rejection into a human-readable diagnosis.
- _Algorithms_: shortest-offending-suffix extraction plus expected-vs-actual symbol-set diff at the deviation state.
- _Failure modes_: overly technical output (raw automaton state IDs) that doesn't map back to a developer's mental model of the agent; mitigated by round-tripping state labels through the abstraction layer's taxonomy so explanations reference tool/action names, not internal state indices.

**M10 — Dashboard / Developer Interface**

- _Purpose_: visualize the inferred automaton, replay traces against it, surface violation/drift alerts, and provide a policy-authoring UI.

**M11 — Orchestration / Scheduler** (cross-cutting)

- _Purpose_: schedule batch relearning, incremental corpus updates, and drift-triggered relearning.

**M12 — Human-in-the-loop Feedback Module** (cross-cutting; introduced beyond the original module list because passive learning alone cannot distinguish "rare-but-legitimate" from "actually anomalous" without some ground truth)

- _Purpose_: capture developer labels on flagged deviations (true anomaly / false positive / legitimate rare path) and feed them back into M4's merge-acceptance thresholds and M8's policy refinement.
- _Failure modes_: label scarcity or label noise from time-pressured developers; addressed by making labeling low-friction (single click from a dashboard alert) rather than a separate workflow.

---

## 5. Semantic Trace Abstraction

This is the most novel component, since without it the learning engine sees an unusably large, framework-fragmented alphabet.

### 5.1 Raw logs to symbolic events

Each adapter emits a raw event; M2 canonicalizes it into a **Canonical Event Schema (CES)** record:

```
{
  agent_id, event_type ∈ {tool_call, tool_result, delegate, retry,
                          memory_read, memory_write, plan_step, error, terminate},
  symbol,            // canonicalized action name
  attributes: { param_schema_hash, status },
  timestamp,
  parent_span_id,    // for recursive delegation
  depth
}
```

`event_type` gives a coarse, framework-independent taxonomy that every adapter must map into (this taxonomy is intentionally small and stable, since it's the join point across frameworks). `symbol` is the fine-grained action identity, which is where cross-framework naming divergence actually shows up.

### 5.2 Mapping frameworks to one alphabet

Each adapter provides a deterministic rule-based mapping from its native event vocabulary to `event_type`. This part is not learned — it is comparatively low-risk hand engineering, one adapter per framework, and is the right place for manual work because the _taxonomy_ (nine event types) is small and stable even though the _frameworks_ proliferate.

### 5.3 Semantic equivalence

The harder problem is `symbol` unification: the same tool called "search_web", "web_search", and "browse" across three frameworks should usually collapse to one symbol, but "delete_record" and "archive_record" should usually not, even though they're superficially similar. TRACE addresses this with embedding-based clustering over a concatenation of tool name, docstring, and parameter schema, using an off-the-shelf sentence/text embedding model — not a bespoke model, consistent with the constraint against unnecessary novel components. Cluster assignment is deterministic post-training (nearest centroid), so re-running abstraction on new traces doesn't silently reshuffle the alphabet.

### 5.4 Reducing state explosion via abstraction

Alphabet size directly bounds the number of distinguishable Myhill–Nerode equivalence classes the learner has to consider, so alphabet compression is not a cosmetic step — it is what makes state merging tractable at all. Recursive delegation is handled by **hierarchical folding**: a sub-agent's entire subtrace is represented in the parent trace as a single `delegate(role)` symbol, with the subtrace itself learned as a separate per-role automaton. This avoids flattening nested calls into the parent alphabet (which would blow up both alphabet size and effective trace length) and avoids needing a pushdown-automaton-class representation for the whole system (see §7).

### 5.5 Evaluating abstraction quality

- **Alphabet compression ratio**: raw distinct symbols vs. canonical symbols.
- **Cluster purity**: on a labeled subsample, fraction of clustered symbols judged semantically equivalent by a human reviewer.
- **Downstream sensitivity**: how much anomaly-detection F1 (§10) changes when the abstraction layer is ablated or its clustering threshold is varied — this is the metric that actually matters, since compression ratio alone can't distinguish good compression from harmful over-merging.

---

## 6. Behavioral Protocol Inference

### 6.1 Candidate algorithms

- **RPNI**: exact, requires positive and negative samples, produces a canonical DFA. Useful as a baseline on synthetic benchmarks where ground truth is known, but negative examples ("this trace is definitely not valid agent behavior") are rarely available in practice.
- **EDSM**: heuristic, evidence-driven merge ordering, works from mostly-positive data, more robust to noise than exact RPNI, no polynomial-time guarantee.
- **ALERGIA / MDI**: designed specifically for learning _probabilistic_ DFAs from positive samples, using a statistical compatibility test (Hoeffding-bound style) to decide whether two states' output/next-symbol distributions are similar enough to merge. This is the closest existing match to TRACE's actual data: frequency-annotated, stochastic, positive-sample-heavy.
- **FlexFringe**: a practical state-merging framework implementing EDSM-style search and probabilistic (ALERGIA-like) variants under one configurable library.

### 6.2 Decision

Build the inference module as an orchestration layer around **FlexFringe**, using its evidence-driven merge search with a probabilistic (ALERGIA-style) compatibility test as the default heuristic, rather than implementing a new learner. RPNI is retained as a secondary baseline specifically for synthetic ground-truth evaluation (§10), where negative examples can be manufactured.

### 6.3 Incremental vs. windowed relearning

True incremental state-merging algorithms exist in the grammatical-inference literature but are less mature and less battle-tested than batch EDSM/ALERGIA. For v1, TRACE uses **windowed batch relearning**: a sliding window of recent traces is relearned periodically, and drift detection (§4, M7) triggers an out-of-cycle relearn. True incremental learning is deferred to the roadmap (§12, Phase 2+) as a lower-risk-first design choice.

### 6.4 Why this fits AI traces

Statistical, frequency-aware merging directly models legitimate stochastic branching (an agent choosing between two equally valid tools) as two probable transitions from one state, rather than forcing the learner to either merge them incorrectly or split them into spuriously distinct states. Evidence-driven merging tolerates the noise floor inherent in LLM-driven action selection better than exact RPNI, which can overfit to whatever negative examples happen to be available. Windowed relearning is the direct answer to non-stationarity.

---

## 7. Formal Model

|Model|Verdict|Reasoning|
|---|---|---|
|DFA|Rejected as sole model|No notion of likelihood; cannot distinguish "rare but valid" from "never seen," which is exactly the distinction agent traces need.|
|NFA|Rejected|Adds nondeterminism as a determinization detail, not probabilistic semantics; doesn't solve the DFA's core gap.|
|**Probabilistic (Deterministic) Automaton / PDFA**|**Selected**|Captures transition likelihood directly, enables principled anomaly scoring via likelihood thresholds, and is exactly the object ALERGIA/MDI learn — no need to invent new learning theory.|
|Timed automata|Deferred|Latency/timeout anomalies matter, but clock constraints add region-graph complexity; approximate cheaply via a side-channel latency monitor instead of embedding clocks in the core model.|
|Pushdown automata|Rejected as core model|Recursive delegation is genuinely stack-like, but general PDA intersection/inclusion problems are far more expensive (undecidable for several natural questions); the hierarchical-composition workaround in §5.4/§9 captures the practically important case (bounded-depth recursion) while staying inside regular-language decidability.|
|Statecharts|Rejected|No mature passive-learning algorithms exist for statecharts from traces; concurrent agents can instead be modeled as per-agent automata plus a lightweight coordination check, sidestepping the need to learn a statechart at all.|
|Petri nets|Deferred to future work|Attractive for concurrency/synchronization and has process-mining precedent (Alpha-miner-style discovery), but existing Petri-net discovery algorithms target low-noise business logs; adapting them to noisy, stochastic agent traces is an open problem, not a solved one to build v1 on.|

**Chosen model**: a PDFA as the core representation, composed hierarchically (one PDFA per delegation level, linked by `delegate(role)` symbols) to approximate bounded recursion without full pushdown cost. This keeps equivalence, minimization, and product construction decidable and well-studied, matching the constraint against inventing new algorithmic machinery, while still capturing the one structurally important non-regular feature of agent traces.

---

## 8. Runtime Verification

**Policy language.** A small DSL over the canonical alphabet Σ, compiled to a policy DFA — e.g., safety obligations like "any `tool_call(delete_*)` must be preceded by a `confirm_step` within the last _k_ symbols." Two policy tracks exist side by side: **learned-conformance** checking (is this trace consistent with the inferred PDFA above a likelihood threshold) and **explicit-policy** checking (hand-written safety/compliance rules compiled to DFA), because these answer different questions — "is this normal" versus "is this allowed" — and conflating them would hide real safety rules inside a purely statistical model.

**Product automata.** The live trace's progress is tracked simultaneously against (a) the learned PDFA's most-likely-consistent state and (b) the explicit policy DFA. A violation is raised when the policy automaton reaches a reject state, when a PDFA transition's probability falls below a tuned threshold ε (statistical anomaly), or when a symbol is entirely unseen from the current state in training (structural anomaly) — these last two are reported as distinct categories, since "rare" and "never seen" warrant different developer responses.

**Counterexample generation.** On violation, M9 emits the shortest offending suffix from the last known-good state, plus the expected symbol set at that state versus the symbol actually observed — an explicit diff, not just a raw log excerpt.

**Behavioral drift detection.** Rolling per-symbol negative log-likelihood under the current model forms a "conformance score" time series; a distributional change test flags drift, which triggers M11 to schedule an out-of-cycle relearn (§6.3, §4/M7).

**Runtime enforcement.** Two operating modes: a synchronous **gate** mode (block or require approval before executing a flagged action) and an asynchronous **observe-and-alert** mode. State tracking is O(1) amortized per event, keeping gate-mode overhead compatible with inline use.

**Monitoring pipeline.** Ingestion → abstraction → per-event state tracking → product-automaton evaluation → alert emission → dashboard/alerting sink, implemented as a streaming/event-driven pipeline (e.g., middleware around MCP server calls or a framework-native callback hook), not a batch-only offline tool.

---

## 9. Theory of Computation Mapping

|TOC concept|Role in TRACE|Included?|
|---|---|---|
|Finite automata|Core representation of the learned protocol (PDFA); states = abstracted execution contexts|Yes|
|Regular languages|Hypothesis class for "the language of normal behavior"; closure properties directly enable product construction|Yes|
|State minimization|Canonicalization (Hopcroft-style) after learning, for stable cross-run comparison and dashboard legibility|Yes|
|Closure properties|Intersection → product of learned-conformance and explicit-policy automata; complementation → "forbidden" language as the complement of an explicit safety language; union → combining multiple models|Yes|
|Decision problems|Membership → O(n) streaming conformance check; emptiness → detect "no possible good continuation remains" as a distinct, stronger violation signal than low probability; equivalence → compares successive relearned models, but is necessary-and-insufficient alone for drift, so it is paired with the probabilistic distance metric (§8), not used by itself|Yes, with explicit caveat|
|Product automata|Combines learned-conformance checking with explicit policy checking; correctness argument: a trace satisfies the combined policy iff its projection is accepted by both components|Yes|
|Model checking|Framed narrowly as _runtime_ verification (checking one execution against a model) — explicitly **not** classical exhaustive state-space model checking of all possible agent executions, to avoid overclaiming|Yes, scoped|
|Complexity theory|State-merging search is NP-hard in general; EDSM's greedy heuristic gives no polynomial guarantee; RPNI's polynomial-time guarantee only holds given a characteristic sample, which stochastic sources don't guarantee. Runtime monitoring itself is cheap (O(1) amortized per event)|Yes|
|Pushdown automata / CFGs|Considered for recursive delegation; **rejected** as the core model due to decidability/complexity cost, replaced by hierarchical PDFA composition (§5.4, §7)|Rejected, noted|
|Turing machines / undecidability|Considered; **rejected** — agent traces don't require unbounded-memory computational power, and no part of the architecture needs a Turing-complete model|Rejected, noted|
|Pumping lemma|Considered; **rejected** — theoretically relevant to regular languages generally, but has no direct architectural role in TRACE and would be a weak, decorative mapping|Rejected, noted|

---

## 10. Evaluation

**Datasets.**

- _Synthetic_: hand-designed ground-truth PDFAs (e.g., a "web research agent" spec) generating traces with controlled noise injection (symbol substitution/insertion/deletion at known rates), enabling direct comparison of inferred vs. true automaton.
- _Real_: traces from example agents built on each supported framework performing comparable task suites (e.g., research, coding-assistant, and customer-support agent scenarios), self-generated rather than scraped from private user data, to sidestep privacy concerns.

**Metrics.**

- Grammar-inference-theoretic: automaton distance to ground truth (language edit distance, or PDFA KL-divergence to the true generating distribution), state-count ratio — synthetic data only.
- Practical: precision/recall/F1 of anomaly detection against traces with injected misbehavior (unauthorized tool sequences, prompt-injection-induced tool-call anomalies, retry/infinite-loop patterns).
- Drift detection: detection latency (traces/events until flagged after an injected distribution shift), false-positive rate under natural non-drift variance.
- Scalability: wall-clock and memory vs. corpus size, alphabet size, and recursion depth.
- Runtime overhead: added per-event monitoring latency, targeted to stay negligible relative to agent tool-call latency.

**Baselines.** N-gram/Markov models over raw (unabstracted) sequences; off-the-shelf process mining (e.g., Heuristics/Inductive Miner) applied naively to agent logs; an LLM-as-judge anomaly detector prompted directly on the trace; plain RPNI on unabstracted traces (isolates the abstraction layer's contribution).

**Ablations.** With/without semantic abstraction; DFA vs. PDFA representation; EDSM vs. ALERGIA merge heuristic; windowed relearning vs. a single static model; hierarchical folding vs. a flat alphabet for recursion-heavy traces.

**Statistical analysis.** Multiple random seeds (agent stochasticity and learner heuristics both introduce randomness); mean ± confidence interval reporting; paired significance testing (e.g., Wilcoxon signed-rank) across ablations, with multiple-comparison correction where many ablations are tested simultaneously.

**Threats to validity.** Synthetic ground-truth automata may under-represent real agent complexity (construct validity); self-generated real traces may not generalize to production-scale enterprise agents (external validity); injected anomalies may not resemble real-world failure modes or attacks (ecological validity); FlexFringe's heuristic randomness could bias comparisons if not controlled (internal validity). Mitigations: multiple seeds, a diverse task suite mixing synthetic and real data, and practitioner review of whether injected anomalies are realistic.

---

## 11. Risks and Mitigations

|Risk category|Specific risk|Mitigation|
|---|---|---|
|Research|The core hypothesis — that passive learning works on stochastic agent traces — may simply not hold at useful accuracy|Run a small pilot (Phase 1/2) before further investment; keep the publication framing flexible enough that a qualified negative result ("here are the limits of passive learning on agentic traces") is still a valid contribution|
|Engineering|Fast-moving framework APIs (LangGraph, CrewAI, MCP, etc.) break adapters|Isolate adapters behind the stable CES; version adapters; contract-test each against framework example traces|
|Scalability|State-merging cost grows with alphabet size and corpus size, with no polynomial guarantee under EDSM|Rely on abstraction-driven alphabet compression, windowed/sampled corpora, and early empirical profiling that feeds back into architecture decisions|
|Algorithmic|Merge heuristic may be too aggressive or conservative for this specific noise profile|Expose merge-acceptance thresholds (e.g., the Hoeffding-bound parameter) as tunable hyperparameters; report a sensitivity analysis, not a single fixed setting|
|Theoretical|No proof that the pipeline preserves the "true" underlying behavior distribution, since both abstraction and the learning heuristic are lossy|State guarantees only where they actually transfer (e.g., RPNI's identification-in-limit result holds only under a characteristic-sample assumption that stochastic sources don't guarantee); position most claims as empirical, with statistical-confidence framing drawn from the ALERGIA/MDI literature where available, rather than as formal correctness proofs|

---

## 12. Roadmap

- **Phase 1 — Prototype (4–6 weeks).** Canonical event schema; adapters for two frameworks (e.g., MCP and LangGraph); RPNI/EDSM via a FlexFringe wrapper, synthetic data only; minimal CLI; prove the pipeline runs end to end.
- **Phase 2 — Research prototype (2–3 months).** Add the semantic abstraction/clustering layer; add ALERGIA/PDFA learning; extend to two more framework adapters; build the product-automaton conformance checker and a basic drift detector; begin collecting/generating real agent traces.
- **Phase 3 — Evaluation (2 months).** Implement the full benchmark suite (synthetic + real, baselines, ablations); run the statistical evaluation; iterate on hyperparameters and merge heuristics based on findings.
- **Phase 4 — Web dashboard (1 month, overlapping Phase 3).** Automaton visualization, trace replay, violation/drift alerting, and a policy-authoring interface.
- **Phase 5 — Publication (1–2 months).** Target a runtime-verification-focused venue or an agent-systems workshop/tool-demo track; scope the submission as a tool paper or short paper depending on the strength of the empirical results from Phase 3, and prepare the open-source artifact to satisfy tool-demo reproducibility requirements. (Check current CFPs at submission time — venue deadlines and formats shift year to year.)

---

## 13. Summary of Rejected Alternatives (for traceability)

To keep the design honest about what was considered and discarded: a new automata-learning algorithm (rejected — existing learners suffice with adaptation); plain DFA as the sole model (rejected — no likelihood semantics); full pushdown automata for the whole system (rejected — decidability/complexity cost disproportionate to the one recursive feature that matters); learned statecharts (rejected — no mature learning algorithms exist); Petri net discovery on raw agent logs (deferred — current discovery algorithms assume low-noise business logs); true incremental grammar induction for v1 (deferred — less mature than batch relearning); timed automata as the core model (deferred — approximate via a latency side-channel instead); Turing-machine-level formalization and the pumping lemma as mapped TOC topics (rejected — no architectural role, would be decorative rather than load-bearing).