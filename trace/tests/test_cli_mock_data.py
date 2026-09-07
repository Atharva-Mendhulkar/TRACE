"""
Integration test executing the complete CLI workflow against mock data.
"""

from pathlib import Path
import pytest

from trace.cli.main import (
    cmd_adapter_list,
    cmd_benchmark_run,
    cmd_feedback_list,
    cmd_feedback_record,
    cmd_ingest,
    cmd_ingest_benchmark,
    cmd_model_inspect,
    cmd_model_list,
    cmd_model_promote,
    cmd_policy_validate,
    cmd_replay,
    cmd_train,
    cmd_validate,
    cmd_verify,
    build_parser,
)
from trace.models.repository import ModelRepository

MOCK_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "mock_data"


def test_full_cli_mock_data_lifecycle(tmp_path):
    db_file = str(tmp_path / "lifecycle.sqlite")
    events_file = str(MOCK_DATA_DIR / "events.json")
    anomalous_file = str(MOCK_DATA_DIR / "anomalous_events.json")
    bench_file = str(MOCK_DATA_DIR / "benchmark_trajectories.json")
    policy_file = str(MOCK_DATA_DIR / "compliance_policy.policy")

    # 1. Adapter list
    assert cmd_adapter_list() == 0

    # 2. Policy validation
    assert cmd_policy_validate(policy_file) == 0

    # 3. Ingestion
    assert cmd_ingest(events_file, framework="mcp", db_path=db_file) == 0
    assert cmd_ingest(anomalous_file, framework="mcp", db_path=db_file) == 0

    # 4. Benchmark Ingestion with training
    assert cmd_ingest_benchmark(bench_file, dataset="swebench", db_path=db_file, train=True) == 0

    # 5. Train model
    parser = build_parser()
    train_args = parser.parse_args([
        "train",
        "--agent-id", "research-agent",
        "--engine", "native-alergia",
        "--heuristic", "alergia",
        "--alpha", "0.05",
        "--db", db_file,
    ])
    assert cmd_train(train_args) == 0

    # 6. Model list & inspect
    repo = ModelRepository(db_file)
    models = repo.list_models(agent_id="research-agent")
    assert len(models) >= 1
    model_id = models[0]["model_id"]

    assert cmd_model_list(agent_id="research-agent", db_path=db_file) == 0
    assert cmd_model_inspect(model_id, db_file) == 0

    # 7. Model validate & promote
    assert cmd_validate(model_id, db_file) == 0
    assert cmd_model_promote(model_id, activate=True, db_path=db_file) == 0

    # 8. Replay
    assert cmd_replay("trace-research-001", db_file) == 0

    # 9. Verify normal trace
    verify_normal_args = parser.parse_args([
        "verify",
        "--trace-id", "trace-research-001",
        "--db", db_file,
    ])
    assert cmd_verify(verify_normal_args) == 0

    # 10. Verify anomalous trace with policy (should detect violation and return 1)
    train_sec_args = parser.parse_args([
        "train",
        "--agent-id", "security-agent",
        "--db", db_file,
    ])
    assert cmd_train(train_sec_args) == 0

    verify_viol_args = parser.parse_args([
        "verify",
        "--trace-id", "trace-violation-001",
        "--policy", policy_file,
        "--db", db_file,
    ])
    assert cmd_verify(verify_viol_args) == 1

    # 11. Feedback
    assert cmd_feedback_record(
        violation_id="viol-mock-001",
        feedback_type="approve",
        reviewer="sec-admin",
        comment="Test comment",
        db_path=db_file,
    ) == 0
    assert cmd_feedback_list(db_file) == 0

    # 12. Benchmark run
    assert cmd_benchmark_run("3") == 0
