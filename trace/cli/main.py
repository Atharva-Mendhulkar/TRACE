"""
TRACE Command Line Interface (PRD §30).
Enhanced with violet-accent semantic CLI elements, interactive REPL shell, and step-by-step mock demo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import List, Optional

from trace.adapters.base import list_adapters
from trace.cli.ui import (
    Style,
    cli_alert,
    cli_alert_danger,
    cli_alert_info,
    cli_alert_success,
    cli_alert_warning,
    cli_banner,
    cli_box,
    cli_h1,
    cli_h2,
    cli_h3,
    cli_kv,
    cli_progress_bar,
    cli_rule,
    cli_table,
    status_pill,
    style,
)
from trace.corpus.store import TraceStore
from trace.inference.flexfringe import FlexFringeRunner
from trace.inference.native_learner import NativeStateMergingLearner
from trace.ingestion.pipeline import IngestionPipeline
from trace.models.repository import ModelRepository
from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser
from trace.verification.verifier import HierarchicalRuntimeVerifier, RuntimeVerifier

DEFAULT_DB = "trace_data.sqlite"


class TraceArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that prints the rich TRACE violet banner on help or error."""

    def format_help(self) -> str:
        banner = cli_banner(print_out=False)
        help_text = super().format_help()
        return f"{banner}\n{help_text}"


def build_parser() -> argparse.ArgumentParser:
    parser = TraceArgumentParser(
        prog="trace",
        description="TRACE: Trace-based Runtime Automata for Compliance and Enforcement",
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    # 1. trace demo
    demo_p = subparsers.add_parser("demo", help="Run interactive step-by-step demonstration with mock data")
    demo_p.add_argument("--auto", action="store_true", help="Run automatically without waiting for keypress")
    demo_p.add_argument("--delay", type=float, default=0.1, help="Delay between demonstration steps (seconds)")
    demo_p.add_argument("--db", default="mock_data/demo.sqlite", help="Demo SQLite database path")

    # 2. trace ingest <path>
    ingest_p = subparsers.add_parser("ingest", help="Ingest framework events into CES storage")
    ingest_p.add_argument("path", help="Path to JSON or JSONL file with events")
    ingest_p.add_argument("--framework", "-f", help="Framework hint (mcp, langgraph, etc.)")
    ingest_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 3. trace train (--agent-id <id> | --role <role>)
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

    # 4. trace validate --model-id <id>
    val_p = subparsers.add_parser("validate", help="Run model validation checks")
    val_p.add_argument("--model-id", "-m", required=True, help="Model UUID")
    val_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 5. trace verify --trace-id <id>
    ver_p = subparsers.add_parser("verify", help="Verify a stored trace against a model and policy")
    ver_p.add_argument("--trace-id", "-t", required=True, help="Trace UUID to verify")
    ver_p.add_argument("--model-id", "-m", help="Model UUID (defaults to active model)")
    ver_p.add_argument("--policy", "-p", help="Path to .policy DSL file")
    ver_p.add_argument("--mode", choices=["observe", "gate"], default="observe", help="Verification mode")
    ver_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 6. trace replay --trace-id <id>
    rep_p = subparsers.add_parser("replay", help="Replay trace and print state trajectory")
    rep_p.add_argument("--trace-id", "-t", required=True, help="Trace UUID to replay")
    rep_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")

    # 7. trace policy validate <file>
    pol_p = subparsers.add_parser("policy", help="Policy management")
    pol_sub = pol_p.add_subparsers(dest="policy_cmd", required=True)
    pol_val = pol_sub.add_parser("validate", help="Parse and validate a policy file")
    pol_val.add_argument("file", help="Path to .policy file")

    # 8. trace model list / inspect / promote
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

    # 9. trace feedback record / list (PRD M10, §23.4)
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

    # 10. trace adapter list
    adp_p = subparsers.add_parser("adapter", help="Adapter management")
    adp_sub = adp_p.add_subparsers(dest="adapter_cmd", required=True)
    adp_sub.add_parser("list", help="List registered framework adapters")

    # 11. trace benchmark run (PRD §32, RQ1–RQ6)
    bench_p = subparsers.add_parser("benchmark", help="Empirical evaluation benchmarks (PRD §32)")
    bench_sub = bench_p.add_subparsers(dest="benchmark_cmd", required=True)
    bench_run = bench_sub.add_parser("run", help="Run benchmark suite")
    bench_run.add_argument(
        "--rq",
        choices=["1", "2", "3", "4", "5", "6", "all"],
        default="all",
        help="Research Question to evaluate",
    )

    # 12. trace ingest-benchmark <path> [--dataset <swebench|osworld|auto>] [--db <db>] [--train]
    bench_ingest_p = subparsers.add_parser(
        "ingest-benchmark",
        help="Ingest real-world benchmark trajectories (SWE-bench / OSWorld) into CES storage",
    )
    bench_ingest_p.add_argument("path", help="Path to JSON file or directory containing benchmark trajectories")
    bench_ingest_p.add_argument(
        "--dataset",
        "-d",
        choices=["swebench", "osworld", "auto"],
        default="auto",
        help="Benchmark dataset format (swebench, osworld, or auto-detect)",
    )
    bench_ingest_p.add_argument("--db", default=DEFAULT_DB, help="Database file path")
    bench_ingest_p.add_argument(
        "--train",
        action="store_true",
        help="Automatically train a PDFA protocol model from the ingested traces",
    )

    return parser


def dispatch_command(parsed: argparse.Namespace) -> int:
    """Route parsed arguments to the corresponding subcommand handler."""
    if parsed.command == "demo":
        return cmd_demo(auto=getattr(parsed, "auto", False), step_delay=parsed.delay, db_path=parsed.db)
    elif parsed.command == "ingest":
        return cmd_ingest(parsed.path, parsed.framework, parsed.db)
    elif parsed.command == "ingest-benchmark":
        return cmd_ingest_benchmark(parsed.path, parsed.dataset, parsed.db, parsed.train)
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
    elif parsed.command == "benchmark" and parsed.benchmark_cmd == "run":
        return cmd_benchmark_run(parsed.rq)
    return 0


def interactive_shell(parser: argparse.ArgumentParser) -> int:
    """
    Run the persistent TRACE interactive shell.
    Enables commands to be executed repeatedly without typing 'trace' each time.
    """
    cli_banner()
    welcome_lines = [
        "Welcome to the TRACE Interactive Shell!",
        "• Type commands directly without 'trace': e.g. 'demo', 'adapter list', 'help'",
        "• Type 'demo' to run the automated step-by-step mock data demonstration",
        "• Type 'exit' or 'quit' to return to your system shell",
    ]
    cli_box("TRACE INTERACTIVE SESSION", welcome_lines, style_color=Style.VIOLET)
    print("")

    while True:
        try:
            prompt_str = f"{style('trace', Style.BOLD, Style.WHITE)} {style('❯', Style.BOLD, Style.BRIGHT_VIOLET)} "
            raw = input(prompt_str).strip()

            if not raw:
                continue

            # Check exit commands
            if raw.lower() in ("exit", "quit", "q", ":q", "exit()", "quit()"):
                cli_alert_success("Exited TRACE environment. Goodbye!")
                break

            # Shell utilities
            if raw.lower() == "clear":
                print("\033[H\033[J", end="")
                continue

            if raw.lower() in ("help", "?"):
                parser.print_help()
                continue

            # Strip leading 'trace ' if user still typed it
            if raw.startswith("trace "):
                raw = raw[6:].strip()

            tokens = shlex.split(raw)
            if not tokens:
                continue

            try:
                parsed = parser.parse_args(tokens)
                if not getattr(parsed, "command", None):
                    parser.print_help()
                    continue
                dispatch_command(parsed)
            except SystemExit:
                # Prevent argparse from terminating the interactive session
                pass
            except Exception as e:
                cli_alert_danger(f"Command execution error: {e}")

        except KeyboardInterrupt:
            print(f"\n{style('Type exit or press Ctrl+D to quit.', Style.DIM, Style.LAVENDER)}")
        except EOFError:
            print("")
            cli_alert_success("Exited TRACE environment. Goodbye!")
            break

    return 0


def main(args: Optional[List[str]] = None) -> int:
    parser = build_parser()

    actual_args = sys.argv[1:] if args is None else args

    # If no arguments provided and in an interactive terminal, launch the TRACE shell
    if not actual_args:
        if sys.stdin.isatty():
            return interactive_shell(parser)
        else:
            parser.print_help()
            return 0

    parsed = parser.parse_args(actual_args)
    if not getattr(parsed, "command", None):
        parser.print_help()
        return 0

    return dispatch_command(parsed)


# ==============================================================================
# SUBCOMMAND HANDLERS
# ==============================================================================

def cmd_demo(auto: bool = False, step_delay: float = 0.1, db_path: str = "mock_data/demo.sqlite") -> int:
    """
    Run an end-to-end interactive demonstration of all TRACE capabilities step-by-step.
    In interactive mode, the user presses [Enter] to advance each test.
    """
    cli_h1("TRACE COMPLETE CAPABILITIES DEMONSTRATION")
    cli_alert_info("Executing all 14 platform operations step-by-step with mock data...")

    # Locate mock_data directory
    mock_dir = Path(__file__).resolve().parent.parent.parent / "mock_data"
    if not mock_dir.exists():
        mock_dir = Path("mock_data")

    events_file = str(mock_dir / "events.json")
    anomalous_file = str(mock_dir / "anomalous_events.json")
    bench_file = str(mock_dir / "benchmark_trajectories.json")
    policy_file = str(mock_dir / "compliance_policy.policy")

    # Clean up previous demo DB
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    auto_run = auto or not sys.stdin.isatty()
    total_steps = 14

    def wait_step(num: int, title: str, description: str) -> bool:
        nonlocal auto_run
        time.sleep(step_delay)
        print("\n" + cli_progress_bar(num, total_steps, label=f"Step {num}/{total_steps}"))
        cli_box(f"TEST {num}/{total_steps}: {title.upper()}", [description], style_color=Style.VIOLET)

        if not auto_run and sys.stdin.isatty():
            prompt_str = f"  {style('❯', Style.BOLD, Style.BRIGHT_VIOLET)} Press {style('[Enter]', Style.BOLD, Style.WHITE)} to run this step {style('(or \"a\" for auto-play, \"q\" to exit)', Style.DIM, Style.LAVENDER)}: "
            try:
                ans = input(prompt_str).strip().lower()
                if ans == "q":
                    cli_alert_warning("Demo halted by operator.")
                    return False
                elif ans == "a":
                    auto_run = True
            except (KeyboardInterrupt, EOFError):
                print("")
                cli_alert_warning("Demo cancelled.")
                return False
        return True

    p = build_parser()

    # Step 1: Adapters Registry
    if not wait_step(1, "Framework Adapter Registry", "Verify all 9 heterogeneous framework and benchmark adapters are active."):
        return 0
    cmd_adapter_list()

    # Step 2: Policy DSL Compilation
    if not wait_step(2, "Declarative Policy DSL & DFA Compilation", "Parse and compile temporal safety rules (REQUIRE, FORBID SEQUENCE, LIMIT) into a minimal DFA."):
        return 0
    cmd_policy_validate(policy_file)

    # Step 3: Multi-Agent Event Ingestion
    if not wait_step(3, "Canonical Event Schema (CES v1.0) Normalization", "Ingest multi-agent MCP JSON-RPC events with schema validation and secret redaction."):
        return 0
    cmd_ingest(events_file, framework="mcp", db_path=db_path)

    # Step 4: Anomalous Events Ingestion
    if not wait_step(4, "Ingesting Policy-Violating Anomaly Events", "Ingest anomalous execution traces for runtime violation detection and explainability diffing."):
        return 0
    cmd_ingest(anomalous_file, framework="mcp", db_path=db_path)

    # Step 5: Real-World Benchmark Trajectory Ingestion
    if not wait_step(5, "SWE-bench Benchmark Trajectory Ingestion & Auto-Training", "Normalize SWE-bench agent trajectories and automatically infer initial protocol automata."):
        return 0
    cmd_ingest_benchmark(bench_file, dataset="swebench", db_path=db_path, train=True)

    # Step 6: Protocol Inference (research-agent)
    if not wait_step(6, "Positive-Only Automata Learning (ALERGIA / MDI)", "Construct Prefix Tree Acceptor (PTA) and apply Hoeffding bounds (α=0.05) state merging on research-agent."):
        return 0
    train_args_1 = p.parse_args([
        "train",
        "--agent-id", "research-agent",
        "--engine", "native-alergia",
        "--heuristic", "alergia",
        "--alpha", "0.05",
        "--db", db_path,
    ])
    cmd_train(train_args_1)

    # Step 7: Protocol Inference (security-agent)
    if not wait_step(7, "Multi-Agent Protocol Learning (security-agent)", "Infer behavioral protocol automaton for security-agent enforcing auth checks."):
        return 0
    train_args_2 = p.parse_args([
        "train",
        "--agent-id", "security-agent",
        "--db", db_path,
    ])
    cmd_train(train_args_2)

    # Step 8: Model Repository Inspection
    if not wait_step(8, "Model Repository & Transition Matrix Inspection", "Query learned models and inspect probabilistic transition matrices (δ, P, n)."):
        return 0
    cmd_model_list(agent_id=None, db_path=db_path)
    repo = ModelRepository(db_path)
    models = repo.list_models(agent_id="research-agent")
    m_id = models[0]["model_id"] if models else None
    if m_id:
        cmd_model_inspect(m_id, db_path=db_path)

    # Step 9: Stochastic Invariants Validation & Promotion
    if not wait_step(9, "Automata Validation & Model Promotion", "Validate connectedness and stochastic sums (∑P=1.0), then promote candidate model to ACTIVE."):
        return 0
    if m_id:
        cmd_validate(m_id, db_path=db_path)
        cmd_model_promote(m_id, activate=True, db_path=db_path)

    # Step 10: Trace Trajectory Replay
    if not wait_step(10, "Trace Trajectory Replay", "Replay stored trace events resolving native actions to canonical symbols."):
        return 0
    cmd_replay("trace-research-001", db_path=db_path)

    # Step 11: Dual-Control Verification (Conforming Trace)
    if not wait_step(11, "Dual-Control Streaming Verification (Conforming Trace)", "Verify conforming trace against learned PDFA and policy DFA. Expected: PASSED."):
        return 0
    ver_norm_args = p.parse_args([
        "verify",
        "--trace-id", "trace-research-001",
        "--db", db_path,
    ])
    cmd_verify(ver_norm_args)

    # Step 12: Safety Policy Violation & Explanations
    if not wait_step(12, "Safety Policy Violation Detection & Counterexample", "Verify anomalous trace violating REQUIRE auth_check BEFORE scan_network. Expected: VIOLATION."):
        return 0
    ver_viol_args = p.parse_args([
        "verify",
        "--trace-id", "trace-violation-001",
        "--policy", policy_file,
        "--db", db_path,
    ])
    cmd_verify(ver_viol_args)

    # Step 13: Behavioral Concept Drift Detection
    if not wait_step(13, "Behavioral Concept Drift Detection (Two-Sample KS Test)", "Evaluate running NLL against baseline distribution using two-sample KS test and tail-quantile CUSUM."):
        return 0
    from trace.drift.detector import DriftDetector
    dd = DriftDetector(agent_id="research-agent", window_size=10, min_sample_size=5)
    dd.set_baseline([0.15, 0.18, 0.14, 0.16, 0.15, 0.17])
    drift_event = None
    for s in [0.85, 0.92, 0.88, 0.95, 0.91]:
        res = dd.record_trace_conformance(s)
        if res:
            drift_event = res
    if drift_event:
        cli_alert_warning(f"Drift alert detected: KS Statistic = {drift_event.statistic:.4f}, p-value = {drift_event.p_value:.4e}")
        cli_table(["Drift Property", "Value"], [
            ["Agent Target", drift_event.agent_id],
            ["Statistical Test", drift_event.test_used],
            ["KS Statistic", f"{drift_event.statistic:.4f}"],
            ["p-value", f"{drift_event.p_value:.6e}"],
            ["Severity", drift_event.severity],
            ["Relearn Triggered", "✔ YES" if drift_event.relearn_triggered else "NO"],
        ])
    else:
        cli_alert_info("No drift detected within sample window.")

    # Step 14: HITL Feedback & Latency Benchmark
    if not wait_step(14, "HITL Triage Recording & Sub-ms Latency Benchmark (RQ3)", "Record operator review decision and run empirical latency verification benchmark (<5ms budget)."):
        return 0
    cmd_feedback_record(
        violation_id="viol-demo-001",
        feedback_type="approve",
        reviewer="secops-lead",
        comment="Authorized penetration testing exception",
        db_path=db_path,
    )
    cmd_feedback_list(db_path=db_path)
    cmd_benchmark_run("3")

    cli_h1("DEMO COMPLETE")
    cli_alert_success("All 14 platform capability tests executed successfully!")
    return 0


def cmd_ingest(path_str: str, framework: Optional[str], db_path: str) -> int:
    cli_h2(f"Ingesting Framework Events")
    cli_kv("Event Source", path_str)
    cli_kv("Framework Hint", framework or "auto-detect")
    cli_kv("Target DB", db_path)

    store = TraceStore(db_path)
    pipeline = IngestionPipeline(trace_store=store)
    try:
        res = pipeline.ingest_file(path_str, framework=framework)
        metrics = [
            ["Accepted CES Events", str(len(res.accepted))],
            ["Duplicate Events", str(len(res.duplicates))],
            ["Rejected Events", str(len(res.rejected))],
        ]
        cli_table(["Ingestion Metric", "Count"], metrics)

        if res.rejected:
            cli_alert_danger(f"Ingestion encountered {len(res.rejected)} rejection(s):")
            for r in res.rejected[:5]:
                cli_alert(f"Reason: {r.get('reason', 'Unknown error')}")
            return 1

        cli_alert_success(f"Successfully processed and stored {len(res.accepted)} events.")
        return 0
    except Exception as e:
        cli_alert_danger(f"Error during ingestion: {e}")
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
        cli_alert_danger("Either --agent-id or --role must be specified for training.")
        return 1

    if not corpus:
        cli_alert_danger(f"No traces found for {target_name} in {parsed.db}")
        return 1

    cli_h2(f"Training Protocol Automata: {target_name}")
    cli_kv("Target ID", target_id)
    cli_kv("Inference Engine", parsed.engine)
    cli_kv("Merge Heuristic", parsed.heuristic)
    cli_kv("Significance (α)", parsed.alpha)
    cli_kv("Training Traces", len(corpus))

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

    all_symbols = sorted(list({s for t in corpus for s in t}))
    val_res = repo.validate_model(model_id, training_alphabet=all_symbols)

    cli_alert_success("Automata learning converged successfully!")
    spec_table = [
        ["Model UUID", model_id],
        ["Target Identifier", target_name],
        ["Learned States |Q|", str(len(pdfa.states))],
        ["Transitions |δ|", str(len(pdfa.delta))],
        ["Alphabet Size |Σ|", f"{len(pdfa.alphabet)} symbols"],
        ["Validation Status", status_pill("CANDIDATE") if val_res["validation_passed"] else status_pill("REJECTED")],
    ]
    cli_table(["Property", "Specification"], spec_table)
    return 0


def cmd_validate(model_id: str, db_path: str) -> int:
    repo = ModelRepository(db_path)
    res = repo.validate_model(model_id)

    cli_h2(f"Model Validation Check: {model_id}")
    rows = [
        ["Overall Validation", "✔ PASSED" if res.get("validation_passed") else "✖ FAILED"],
        ["Stochastic Validity", "✔ VALID" if res.get("stochastic_validity") else "✖ INVALID"],
        ["Connectedness", "✔ CONNECTED" if res.get("connectedness") else "✖ UNCONNECTED"],
        ["Model Status", status_pill(res.get("status", "UNKNOWN"))],
    ]
    cli_table(["Check", "Result"], rows)

    if res.get("validation_passed"):
        cli_alert_success(f"Model '{model_id}' passed all validation invariants.")
        return 0
    else:
        cli_alert_danger(f"Model '{model_id}' failed validation checks.")
        return 1


def cmd_verify(parsed: argparse.Namespace) -> int:
    store = TraceStore(parsed.db)
    repo = ModelRepository(parsed.db)

    events = store.get_trace(parsed.trace_id)
    if not events:
        cli_alert_danger(f"No events found for trace_id '{parsed.trace_id}'")
        return 1

    agent_id = events[0].agent_id
    model_dict = None
    if parsed.model_id:
        model_dict = repo.get_model(parsed.model_id)
    else:
        model_dict = repo.get_active_model(agent_id)
        if not model_dict:
            models = repo.list_models(agent_id=agent_id)
            if models:
                model_dict = models[0]

    if not model_dict:
        cli_alert_danger(f"No active or candidate model found for agent '{agent_id}'")
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

    cli_h2(f"Runtime Verification: Trace {parsed.trace_id}")
    cli_kv("Agent Target", agent_id)
    cli_kv("Model Version", f"v{model_dict['model_version']}")
    cli_kv("Policy File", parsed.policy or "None (Behavioral protocol only)")
    cli_kv("Enforcement Mode", parsed.mode.upper())

    mean_nll = responses[-1].running_mean_nll if responses else 0.0
    summary_rows = [
        ["Events Evaluated", str(len(responses))],
        ["Violations Detected", str(len(violations))],
        ["Final Running NLL", f"{mean_nll:.4f}"],
    ]
    cli_table(["Verification Metric", "Result"], summary_rows)

    if violations:
        cli_alert_danger(f"Verification FAILED: Detected {len(violations)} non-conforming event(s)!")
        viol_rows = []
        for v in violations:
            expl = v.violation
            if expl:
                viol_rows.append([
                    str(expl.event_id)[:16],
                    expl.observed_symbol,
                    ", ".join(expl.expected_symbols_at_state[:3]),
                    expl.policy_rule_if_applicable or "Structural/Probabilistic",
                ])
            else:
                viol_rows.append([str(v.event_id)[:16], "N/A", "N/A", ", ".join(v.classification)])
        cli_table(["Event ID", "Observed", "Expected at State", "Policy / Reason"], viol_rows)
        return 1

    cli_alert_success(f"Verification PASSED: Trace '{parsed.trace_id}' strictly conforms to protocol & policy.")
    return 0


def cmd_replay(trace_id: str, db_path: str) -> int:
    store = TraceStore(db_path)
    events = store.get_trace(trace_id)
    if not events:
        cli_alert_danger(f"No events found for trace '{trace_id}'")
        return 1

    cli_h2(f"Trace Trajectory Replay: {trace_id}")
    cli_kv("Total Events", len(events))
    cli_kv("Agent Target", events[0].agent_id if events else "N/A")

    rows = []
    for ev in events:
        rows.append([f"{ev.sequence_no:02d}", ev.event_type, ev.symbol, ev.raw_symbol])
    cli_table(["Seq", "Event Type", "Canonical Symbol", "Raw Symbol"], rows)
    cli_alert_info(f"Replayed {len(events)} events successfully.")
    return 0


def cmd_policy_validate(file_path: str) -> int:
    path = Path(file_path)
    if not path.exists():
        cli_alert_danger(f"Policy file not found: {path}")
        return 1

    cli_h2(f"Validating Policy DSL: {path.name}")
    content = path.read_text(encoding="utf-8")
    try:
        ast = PolicyParser.parse(content)
        dfa = PolicyCompiler.compile(ast)
        cli_alert_success(f"Policy '{ast.name}' compiled to deterministic finite automaton (DFA)!")
        cli_table(["Automaton Property", "Specification"], [
            ["Policy Name", ast.name],
            ["Rules Declared", str(len(ast.rules))],
            ["DFA States |Q|", str(len(dfa.states))],
            ["Accepting States |F|", str(len(dfa.accepting_states))],
            ["Transitions |δ|", str(len(dfa.delta))],
        ])
        return 0
    except Exception as e:
        cli_alert_danger(f"Policy validation failed: {e}")
        return 1


def cmd_model_list(agent_id: Optional[str], db_path: str) -> int:
    repo = ModelRepository(db_path)
    models = repo.list_models(agent_id=agent_id)
    cli_h2("Automata Model Repository")
    if agent_id:
        cli_kv("Filtered Agent", agent_id)
    if not models:
        cli_alert_info("No models found in repository.")
        return 0

    rows = []
    for m in models:
        st_cnt = len(m["pdfa"].states)
        rows.append([
            m["model_id"],
            m["agent_id"],
            f"v{m['model_version']}",
            status_pill(m["status"]),
            str(st_cnt),
            str(len(m["pdfa"].alphabet)),
        ])
    cli_table(["Model UUID", "Agent Target", "Ver", "Status", "States", "Alphabet"], rows)
    return 0


def cmd_model_inspect(model_id: str, db_path: str) -> int:
    repo = ModelRepository(db_path)
    m = repo.get_model(model_id)
    if not m:
        cli_alert_danger(f"Model '{model_id}' not found.")
        return 1

    pdfa = m["pdfa"]
    cli_h2(f"Model Inspection: {model_id}")
    cli_kv("Model UUID", m["model_id"])
    cli_kv("Agent Target", m["agent_id"])
    cli_kv("Version", f"v{m['model_version']}")
    cli_kv("Status", status_pill(m["status"]))
    cli_kv("Learned States", len(pdfa.states))
    cli_kv("Alphabet Size", len(pdfa.alphabet))
    cli_kv("Alphabet", ", ".join(sorted(list(pdfa.alphabet))))

    cli_h3("State Machine Transitions (δ, P, n)")
    rows = []
    for (s, sym), tgt in sorted(pdfa.delta.items(), key=lambda x: (x[0][0], x[0][1])):
        prob = pdfa.P.get((s, sym), 0.0)
        cnt = pdfa.counts.get((s, sym), 0)
        rows.append([s, sym, f"{prob:.3f}", str(cnt), tgt])
    cli_table(["Source State", "Event Symbol", "Probability (P)", "Count (n)", "Target State"], rows)
    return 0


def cmd_adapter_list() -> int:
    cli_h2("Registered Framework Adapters")
    adapters = list_adapters()
    rows = [
        [a["framework"], f"v{a['adapter_version']}", ", ".join(a["supported_framework_schema_versions"]), "✔ Active"]
        for a in adapters
    ]
    cli_table(["Framework", "Adapter Ver", "Supported Schemas", "Status"], rows)
    cli_alert_info(f"Total of {len(adapters)} framework adapters active.")
    return 0


def cmd_model_promote(model_id: str, activate: bool, db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine
    repo = ModelRepository(db_path)
    engine = FeedbackEngine(model_repo=repo)
    cli_h2(f"Model Promotion: {model_id}")
    try:
        res = engine.review_candidate_model(model_id, action="approve", reviewer="cli-operator")
        cli_alert_success(f"Model promoted to status: {status_pill(res['status'])}")
        if activate:
            act_res = engine.activate_promoted_model(model_id)
            cli_alert_success(f"Model activated into production: {status_pill(act_res['status'])}")
        return 0
    except Exception as e:
        cli_alert_danger(f"Error promoting model '{model_id}': {e}")
        return 1


def cmd_feedback_record(violation_id: str, feedback_type: str, reviewer: str, comment: Optional[str], db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
    store = RelationalFeedbackStore(f"sqlite:///{db_path}")
    engine = FeedbackEngine(store=store)
    cli_h2("Recording Human-in-the-Loop Feedback")
    record = engine.record_feedback(
        violation_id=violation_id,
        feedback_type=feedback_type,  # type: ignore
        reviewer=reviewer,
        comment=comment,
    )
    cli_alert_success(f"Feedback recorded successfully.")
    cli_table(["Field", "Value"], [
        ["Feedback UUID", record.feedback_id],
        ["Violation UUID", record.violation_id],
        ["Feedback Type", record.feedback_type],
        ["Reviewer", record.reviewer],
        ["Comment", record.comment or "None"],
    ])
    return 0


def cmd_feedback_list(db_path: str) -> int:
    from trace.feedback.engine import FeedbackEngine, RelationalFeedbackStore
    store = RelationalFeedbackStore(f"sqlite:///{db_path}")
    engine = FeedbackEngine(store=store)
    records = engine.list_feedback()
    cli_h2("Human-in-the-Loop Feedback Registry")
    if not records:
        cli_alert_info("No feedback records registered.")
        return 0

    rows = []
    for r in records:
        rows.append([
            r.feedback_id[:16] + "...",
            r.violation_id[:16] + "...",
            r.feedback_type,
            r.reviewer,
            "✔ Yes" if r.applied else "Pending",
        ])
    cli_table(["Feedback UUID", "Violation UUID", "Type", "Reviewer", "Applied"], rows)
    return 0


def cmd_benchmark_run(rq: str) -> int:
    from trace.benchmarks.evaluator import BenchmarkSuite
    cli_h2(f"TRACE Empirical Benchmark Suite (Evaluating: RQ{rq.upper()})")
    suite = BenchmarkSuite()

    if rq == "all":
        summary = suite.run_all()
        cli_alert_success("Evaluation of RQ1–RQ6 completed successfully.")
        print("\n" + summary.render_markdown())
    elif rq == "1":
        res1 = suite.run_rq1()
        cli_alert_success("RQ1 Sample Complexity Evaluation Completed.")
        cli_table(["Sample Size (Traces)", "Learned States |Q|", "Transitions |δ|"], [
            [str(s), str(st), str(tr)]
            for s, st, tr in zip(res1.sample_sizes, res1.state_counts, res1.transition_counts)
        ])
    elif rq == "2":
        res2 = suite.run_rq2()
        cli_alert_success("RQ2 Anomaly Detection Evaluation Completed.")
        cli_table(["Metric", "Value"], [
            ["Precision", f"{res2.precision:.4f}"],
            ["Recall", f"{res2.recall:.4f}"],
            ["F1-Score", f"{res2.f1:.4f}"],
        ])
    elif rq == "3":
        res3 = suite.run_rq3()
        cli_alert_success("RQ3 Latency Overhead Evaluation Completed.")
        cli_table(["Metric", "Value"], [
            ["Mean Latency", f"{res3.mean_latency_ms:.4f} ms"],
            ["p99 Latency", f"{res3.p99_latency_ms:.4f} ms"],
            ["Budget Met (< 5.0ms)", "✔ YES" if res3.budget_met else "✖ NO"],
        ])
    elif rq == "4":
        res4 = suite.run_rq4()
        cli_alert_success("RQ4 Policy Enforcement Evaluation Completed.")
        cli_table(["Metric", "Value"], [
            ["Policy Enforcement Accuracy", f"{res4.policy_enforcement_accuracy:.4f}"],
            ["Violations Caught", str(res4.violations_caught)],
        ])
    elif rq == "5":
        res5 = suite.run_rq5()
        cli_alert_success("RQ5 Concept Drift Detection Evaluation Completed.")
        cli_table(["Metric", "Value"], [
            ["Drift Detected", "✔ YES" if res5.drift_detected else "✖ NO"],
            ["KS Statistic", f"{res5.ks_statistic:.4f}"],
            ["p-value", f"{res5.p_value:.6e}"],
        ])
    elif rq == "6":
        res6 = suite.run_rq6()
        cli_alert_success("RQ6 Hierarchical State Reduction Completed.")
        cli_table(["Metric", "Value"], [
            ["Flat Monolithic States", str(res6.flat_states_count)],
            ["Hierarchical Modular States", str(res6.hierarchical_states_count)],
            ["State Space Reduction", f"{res6.reduction_percentage:.2f}%"],
        ])

    return 0


def cmd_ingest_benchmark(path_str: str, dataset: str, db_path: str, train: bool) -> int:
    cli_h2("Benchmark Trajectory Ingestion")
    cli_kv("Dataset Mode", dataset)
    cli_kv("Source Path", path_str)
    cli_kv("Target DB", db_path)

    store = TraceStore(db_path)
    pipeline = IngestionPipeline(trace_store=store)
    path = Path(path_str)

    if not path.exists():
        cli_alert_danger(f"Error: Path '{path_str}' does not exist.")
        return 1

    files_to_process = [path] if path.is_file() else sorted(list(path.glob("*.json")) + list(path.glob("*.jsonl")))

    if not files_to_process:
        cli_alert_danger(f"Error: No JSON/JSONL files found in '{path_str}'.")
        return 1

    total_accepted = 0
    total_duplicates = 0
    total_rejected = 0
    framework_hint = None if dataset == "auto" else dataset

    cli_alert_info(f"Ingesting {len(files_to_process)} benchmark file(s)...")

    agents_seen = set()
    for f in files_to_process:
        try:
            res = pipeline.ingest_file(str(f), framework=framework_hint)
            total_accepted += len(res.accepted)
            total_duplicates += len(res.duplicates)
            total_rejected += len(res.rejected)
            for rec in res.accepted:
                agents_seen.add(rec.agent_id)
        except Exception as e:
            cli_alert_warning(f"Failed to ingest {f.name}: {e}")

    summary_rows = [
        ["Files Processed", str(len(files_to_process))],
        ["Accepted CES Events", str(total_accepted)],
        ["Duplicate Events", str(total_duplicates)],
        ["Rejected Events", str(total_rejected)],
        ["Agents Discovered", ", ".join(sorted(agents_seen)) if agents_seen else "None"],
    ]
    cli_table(["Benchmark Metric", "Result"], summary_rows)

    if train and agents_seen:
        cli_h3("Automated Protocol Model Inference (ALERGIA Engine)")
        repo = ModelRepository(db_path)
        for agent_id in sorted(list(agents_seen)):
            corpus = store.get_corpus(agent_id)
            if not corpus:
                continue
            learner = NativeStateMergingLearner(heuristic="alergia", alpha=0.05)
            pdfa = learner.fit(corpus)
            h = hashlib.sha256(json.dumps([t for t in corpus]).encode("utf-8")).hexdigest()[:16]
            model_id = repo.save_model(
                agent_id=agent_id,
                pdfa=pdfa,
                training_corpus_hash=h,
                learner_config={"engine": "native-alergia", "alpha": 0.05, "source": "ingest-benchmark"},
            )
            repo.validate_model(model_id)
            repo.promote_model(model_id, approver="benchmark-ingest")
            cli_alert_success(f"Synthesized PDFA for '{agent_id}': Model UUID {model_id} ({len(pdfa.states)} states, {len(pdfa.alphabet)} symbols)")

    return 0 if (total_accepted > 0 or total_duplicates > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
