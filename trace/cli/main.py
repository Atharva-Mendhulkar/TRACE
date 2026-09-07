"""
TRACE Command Line Interface (PRD §30).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional

from trace.adapters.base import list_adapters
from trace.corpus.store import TraceStore
from trace.inference.flexfringe import FlexFringeRunner
from trace.inference.native_learner import NativeStateMergingLearner
from trace.ingestion.pipeline import IngestionPipeline
from trace.models.repository import ModelRepository
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.verification.verifier import HierarchicalRuntimeVerifier, RuntimeVerifier

DEFAULT_DB = "trace_data.sqlite"


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trace",
        description="TRACE: Trace-based Runtime Automata for Compliance and Enforcement",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. trace ingest <path>
    ingest_p = subparsers.add_parser("ingest", help="Ingest framework events into CES storage")
    ingest_p.add_argument("path", help="Path to JSON or JSONL file with events")
    ingest_p.add_argument("--framework", "-f", help="Framework hint (mcp, langgraph, etc.)")
    ingest_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 2. trace train (--agent-id <id> | --role <role>)
    train_p = subparsers.add_parser("train", help="Train a PDFA from stored traces")
    train_p.add_argument("--agent-id", "-a", help="Agent ID to train model for")
    train_p.add_argument("--role", "-r", help="Delegation role to train child model for (PRD §16.2)")
    train_p.add_argument(
        "--engine",
        choices=["native", "native-alergia", "native-edsm", "native-rpni", "flexfringe"],
        default="native-alergia",
        help="Inference engine (native-alergia, native-edsm, native-rpni, flexfringe)",
    )
    train_p.add_argument("--heuristic", choices=["alergia", "edsm"], default="alergia", help="Merge heuristic")
    train_p.add_argument("--alpha", type=float, default=0.05, help="Hoeffding bound significance alpha")
    train_p.add_argument("--include-truncated", action="store_true", help="Include truncated traces in training")
    train_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 3. trace validate --model-id <id>
    val_p = subparsers.add_parser("validate", help="Run model validation checks")
    val_p.add_argument("--model-id", "-m", required=True, help="Model UUID")
    val_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 4. trace verify --trace-id <id>
    ver_p = subparsers.add_parser("verify", help="Verify a stored trace against a model and policy")
    ver_p.add_argument("--trace-id", "-t", required=True, help="Trace UUID to verify")
    ver_p.add_argument("--model-id", "-m", help="Model UUID (defaults to active model)")
    ver_p.add_argument("--policy", "-p", help="Path to .policy DSL file")
    ver_p.add_argument("--mode", choices=["observe", "gate"], default="observe", help="Verification mode")
    ver_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 5. trace replay --trace-id <id>
    rep_p = subparsers.add_parser("replay", help="Replay trace and print state trajectory")
    rep_p.add_argument("--trace-id", "-t", required=True, help="Trace UUID to replay")
    rep_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 6. trace policy validate <file>
    pol_p = subparsers.add_parser("policy", help="Policy management")
    pol_sub = pol_p.add_subparsers(dest="policy_cmd", required=True)
    pol_val = pol_sub.add_parser("validate", help="Parse and validate a policy file")
    pol_val.add_argument("file", help="Path to .policy file")

    # 7. trace model list / inspect
    mod_p = subparsers.add_parser("model", help="Model repository management")
    mod_sub = mod_p.add_subparsers(dest="model_cmd", required=True)
    mod_list = mod_sub.add_parser("list", help="List trained models")
    mod_list.add_argument("--agent-id", "-a", help="Filter by agent id")
    mod_list.add_argument("--db", default=DEFAULT_DB, help="Database file path")
    mod_insp = mod_sub.add_parser("inspect", help="Inspect a model")
    mod_insp.add_argument("model_id", help="Model UUID")
    mod_insp.add_argument("--db", default=DEFAULT_DB, help="Database file path")
    mod_prom = mod_sub.add_parser("promote", help="Promote a candidate model (PRD M10, §23.4)")
    mod_prom.add_argument("model_id", help="Model UUID")
    mod_prom.add_argument("--activate", action="store_true", help="Also activate model into production")
    mod_prom.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 8. trace feedback record / list (PRD M10, §23.4)
    fb_p = subparsers.add_parser("feedback", help="Human-in-the-loop violation feedback")
    fb_sub = fb_p.add_subparsers(dest="feedback_cmd", required=True)
    fb_rec = fb_sub.add_parser("record", help="Record reviewer feedback on a violation")
    fb_rec.add_argument("--violation-id", "-v", required=True, help="Violation UUID")
    fb_rec.add_argument(
        "--type",
        "-t",
        required=True,
        choices=["approve", "reject", "override_transition"],
        help="Feedback type",
    )
    fb_rec.add_argument("--reviewer", "-r", required=True, help="Reviewer username")
    fb_rec.add_argument("--comment", "-c", help="Review comment")
    fb_rec.add_argument("--db", default=DEFAULT_DB, help="Database file path")
    fb_list = fb_sub.add_parser("list", help="List recorded feedback")
    fb_list.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 9. trace adapter list
    adp_p = subparsers.add_parser("adapter", help="Adapter management")
    adp_sub = adp_p.add_subparsers(dest="adapter_cmd", required=True)
    adp_sub.add_parser("list", help="List registered framework adapters")

    parsed = parser.parse_args(args)

    # Dispatch commands
    if parsed.command == "ingest":
        return cmd_ingest(parsed.path, parsed.framework, parsed.db)
    elif parsed.command == "train":
        return cmd_train(parsed)
    elif parsed.command == "validate":
        return cmd_validate(parsed.model_id, parsed.db)
    elif parsed.command == "verify":
        return cmd_verify(parsed)
    elif parsed.command == "replay":
        return cmd_replay(parsed.trace_id, parsed.db)
    elif parsed.command == "policy" and parsed.policy_cmd == "validate":
        return cmd_policy_validate(parsed.file)
    elif parsed.command == "model":
        if parsed.model_cmd == "list":
            return cmd_model_list(parsed.agent_id, parsed.db)
        elif parsed.model_cmd == "inspect":
            return cmd_model_inspect(parsed.model_id, parsed.db)
        elif parsed.model_cmd == "promote":
            return cmd_model_promote(parsed.model_id, parsed.activate, parsed.db)
    elif parsed.command == "feedback":
        if parsed.feedback_cmd == "record":
            return cmd_feedback_record(parsed.violation_id, parsed.type, parsed.reviewer, parsed.comment, parsed.db)
        elif parsed.feedback_cmd == "list":
            return cmd_feedback_list(parsed.db)
    elif parsed.command == "adapter" and parsed.adapter_cmd == "list":
        return cmd_adapter_list()

    return 0


def cmd_ingest(path_str: str, framework: Optional[str], db_path: str) -> int:
    store = TraceStore(db_path)
    pipeline = IngestionPipeline(trace_store=store)
    try:
        res = pipeline.ingest_file(path_str, framework=framework)
        print(f"Ingestion summary for {path_str}:")
        print(f"  Accepted:   {len(res.accepted)}")
        print(f"  Duplicates: {len(res.duplicates)}")
        print(f"  Rejected:   {len(res.rejected)}")
        if res.rejected:
            print("\nRejections detail:")
            for r in res.rejected[:5]:
                print(f"  - {r.get('reason')}")
        return 0 if not res.rejected else 1
    except Exception as e:
        print(f"Error during ingestion: {e}", file=sys.stderr)
        return 1


def cmd_train(parsed: argparse.Namespace) -> int:
    store = TraceStore(parsed.db)
    repo = ModelRepository(parsed.db)

    if parsed.role:
        corpus = store.get_role_corpus(parsed.role, include_truncated=parsed.include_truncated)
        target_name = f"role '{parsed.role}'"
        target_id = f"role:{parsed.role}"
    elif parsed.agent_id:
        corpus = store.get_corpus(parsed.agent_id, include_truncated=parsed.include_truncated)
        target_name = f"agent '{parsed.agent_id}'"
        target_id = parsed.agent_id
    else:
        print("Error: Either --agent-id or --role must be specified for training.", file=sys.stderr)
        return 1

    if not corpus:
        print(f"No traces found for {target_name} in {parsed.db}", file=sys.stderr)
        return 1

    print(f"Loaded {len(corpus)} traces for training {target_name}.")
    corpus_hash = hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()

    # Train via selected engine
    from trace.inference import get_learner_engine

    kwargs = {}
    if "alergia" in parsed.engine or parsed.engine == "native":
        kwargs["alpha"] = parsed.alpha
    elif parsed.engine == "flexfringe":
        kwargs["heuristic"] = parsed.heuristic
        kwargs["alpha"] = parsed.alpha

    engine = get_learner_engine(parsed.engine, **kwargs)
    pdfa = engine.fit(corpus)

    config = {
        "engine": getattr(engine, "name", parsed.engine),
        "heuristic": parsed.heuristic,
        "alpha": parsed.alpha,
        "include_truncated": parsed.include_truncated,
        "role": parsed.role,
    }
    model_id = repo.save_model(
        agent_id=target_id,
        role=parsed.role,
        pdfa=pdfa,
        training_corpus_hash=corpus_hash,
        learner_config=config,
    )

    print(f"Model trained successfully!")
    print(f"  Model ID:     {model_id}")
    print(f"  Target:       {target_name}")
    print(f"  States:       {len(pdfa.states)}")
    print(f"  Transitions:  {len(pdfa.delta)}")
    print(f"  Alphabet:     {sorted(list(pdfa.alphabet))}")

    # Run auto-validation
    all_symbols = sorted(list({s for t in corpus for s in t}))
    val_res = repo.validate_model(model_id, training_alphabet=all_symbols)
    print(f"  Validation:   {'PASSED (Status: CANDIDATE)' if val_res['validation_passed'] else 'FAILED'}")
    return 0


def cmd_validate(model_id: str, db_path: str) -> int:
    repo = ModelRepository(db_path)
    res = repo.validate_model(model_id)
    print(f"Validation results for model {model_id}:")
    print(json.dumps(res, indent=2))
    return 0 if res["validation_passed"] else 1


def cmd_verify(parsed: argparse.Namespace) -> int:
    store = TraceStore(parsed.db)
    repo = ModelRepository(parsed.db)

    events = store.get_trace(parsed.trace_id)
    if not events:
        print(f"No events found for trace_id '{parsed.trace_id}'", file=sys.stderr)
        return 1

    agent_id = events[0].agent_id
    model_dict = None
    if parsed.model_id:
        model_dict = repo.get_model(parsed.model_id)
    else:
        model_dict = repo.get_active_model(agent_id)
        if not model_dict:
            # Fallback to latest candidate
            models = repo.list_models(agent_id=agent_id)
            if models:
                model_dict = models[0]

    if not model_dict:
        print(f"No model found for agent '{agent_id}'", file=sys.stderr)
        return 1

    pdfa = model_dict["pdfa"]
    policy_dfa = None
    if parsed.policy:
        ast = PolicyParser.parse(Path(parsed.policy).read_text(encoding="utf-8"))
        policy_dfa = PolicyCompiler.compile(ast, alphabet=pdfa.alphabet)

    def role_resolver(role_name: str) -> Optional[PDFA]:
        m = repo.get_active_model_for_role(role_name)
        return m["pdfa"] if m else None

    verifier = HierarchicalRuntimeVerifier(
        parent_pdfa=pdfa,
        role_pdfa_resolver=role_resolver,
        parent_policy_dfa=policy_dfa,
        mode=parsed.mode,
        model_version=f"v{model_dict['model_version']}",
    )

    responses = verifier.replay_trace(events)
    violations = [
        r for r in responses
        if r.violation is not None or "missing_child_trace" in r.classification
    ]

    print(f"Verification Results for Trace {parsed.trace_id}:")
    print(f"  Events Evaluated: {len(responses)}")
    print(f"  Violations Found: {len(violations)}")
    print(f"  Mean NLL:         {responses[-1].running_mean_nll if responses else 0.0:.3f}")

    if violations:
        print("\nViolations Summary:")
        for v in violations:
            expl = v.violation
            if expl:
                print(f"  - Event: {expl.event_id} | Classifications: {v.classification}")
                print(f"    Observed: {expl.observed_symbol} | Expected at {expl.previous_known_good_state.state_id}: {expl.expected_symbols_at_state}")
                if expl.delegation_context.depth > 0 or expl.delegation_context.role:
                    print(f"    Delegation: depth={expl.delegation_context.depth}, role='{expl.delegation_context.role}', parent_span='{expl.delegation_context.parent_span_id}'")
                if expl.policy_rule_if_applicable:
                    print(f"    Policy Rule Broken: {expl.policy_rule_if_applicable}")
            else:
                print(f"  - Event: {v.event_id} | Classifications: {v.classification}")
        return 1
    return 0


def cmd_replay(trace_id: str, db_path: str) -> int:
    store = TraceStore(db_path)
    events = store.get_trace(trace_id)
    if not events:
        print(f"No events found for trace '{trace_id}'", file=sys.stderr)
        return 1

    print(f"Replaying trace {trace_id} ({len(events)} events):")
    for ev in events:
        print(f"  [{ev.sequence_no:02d}] {ev.event_type:<12} | {ev.symbol:<20} (native: {ev.raw_symbol})")
    return 0


def cmd_policy_validate(file_path: str) -> int:
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    try:
        ast = PolicyParser.parse(content)
        dfa = PolicyCompiler.compile(ast)
        print(f"Policy '{ast.name}' compiled successfully!")
        print(f"  Rules count:      {len(ast.rules)}")
        print(f"  DFA States:       {len(dfa.states)}")
        print(f"  Accepting States: {len(dfa.accepting_states)}")
        print(f"  Transitions:      {len(dfa.delta)}")
        return 0
    except Exception as e:
        print(f"Policy validation failed: {e}", file=sys.stderr)
        return 1


def cmd_model_list(agent_id: Optional[str], db_path: str) -> int:
    repo = ModelRepository(db_path)
    models = repo.list_models(agent_id=agent_id)
    if not models:
        print("No models found.")
        return 0

    print(f"{'Model ID':<38} | {'Agent':<15} | {'Ver':<4} | {'Status':<12} | {'States':<6}")
    print("-" * 85)
    for m in models:
        st_cnt = len(m["pdfa"].states)
        print(f"{m['model_id']:<38} | {m['agent_id']:<15} | {m['model_version']:<4} | {m['status']:<12} | {st_cnt:<6}")
    return 0


def cmd_model_inspect(model_id: str, db_path: str) -> int:
    repo = ModelRepository(db_path)
    m = repo.get_model(model_id)
    if not m:
        print(f"Model '{model_id}' not found.", file=sys.stderr)
        return 1

    pdfa = m["pdfa"]
    print(f"Model ID:      {m['model_id']}")
    print(f"Agent ID:      {m['agent_id']}")
    print(f"Version:       v{m['model_version']}")
    print(f"Status:        {m['status']}")
    print(f"States:        {len(pdfa.states)}")
    print(f"Alphabet:      {sorted(list(pdfa.alphabet))}")
    print("\nState Machine Transitions:")
    for (s, sym), tgt in sorted(pdfa.delta.items(), key=lambda x: (x[0][0], x[0][1])):
        prob = pdfa.P.get((s, sym), 0.0)
        cnt = pdfa.counts.get((s, sym), 0)
        print(f"  {s} --[{sym} (P={prob:.2f}, n={cnt})]--> {tgt}")
    return 0


def cmd_adapter_list() -> int:
    adapters = list_adapters()
    print("Registered Framework Adapters:")
    for a in adapters:
        print(f"  Framework: {a['framework']:<18} | Adapter Ver: {a['adapter_version']:<6} | Supported Schemas: {', '.join(a['supported_framework_schema_versions'])}")
    return 0


def cmd_model_promote(model_id: str, activate: bool, db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine
    repo = ModelRepository(db_path)
    engine = FeedbackEngine(model_repo=repo)
    try:
        res = engine.review_candidate_model(model_id, action="approve", reviewer="cli-operator")
        print(f"Model '{model_id}' promoted to status: {res['status']}")
        if activate:
            act_res = engine.activate_promoted_model(model_id)
            print(f"Model '{model_id}' activated into production: {act_res['status']}")
        return 0
    except Exception as e:
        print(f"Error promoting model '{model_id}': {e}", file=sys.stderr)
        return 1


def cmd_feedback_record(violation_id: str, feedback_type: str, reviewer: str, comment: Optional[str], db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
    store = RelationalFeedbackStore(f"sqlite:///{db_path}")
    engine = FeedbackEngine(store=store)
    record = engine.record_feedback(
        violation_id=violation_id,
        feedback_type=feedback_type,  # type: ignore
        reviewer=reviewer,
        comment=comment,
    )
    print(f"Recorded feedback ID: {record.feedback_id}")
    print(f"  Violation ID:  {record.violation_id}")
    print(f"  Type:          {record.feedback_type}")
    print(f"  Reviewer:      {record.reviewer}")
    if record.comment:
        print(f"  Comment:       {record.comment}")
    return 0


def cmd_feedback_list(db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
    store = RelationalFeedbackStore(f"sqlite:///{db_path}")
    engine = FeedbackEngine(store=store)
    records = engine.list_feedback()
    if not records:
        print("No feedback records found.")
        return 0

    print(f"{'Feedback ID':<38} | {'Violation ID':<38} | {'Type':<12} | {'Reviewer':<15} | {'Applied':<7}")
    print("-" * 125)
    for r in records:
        print(f"{r.feedback_id:<38} | {r.violation_id:<38} | {r.feedback_type:<12} | {r.reviewer:<15} | {str(r.applied):<7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
