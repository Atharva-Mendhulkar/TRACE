"""
Unit and Integration Tests for Real-World Benchmark Importers (SWE-bench & OSWorld).
"""

import json
from pathlib import Path
import pytest
from uuid import uuid4

from trace.adapters.base import get_adapter
from trace.adapters.swebench import SWEBenchAdapter
from trace.adapters.osworld import OSWorldAdapter
from trace.cli.main import cmd_ingest_benchmark
from trace.corpus.store import TraceStore
from trace.ingestion.pipeline import IngestionPipeline
from trace.models.repository import ModelRepository
from trace.schema.models import validate_ces_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_swebench_adapter_fixture_contract():
    fixture_path = FIXTURES_DIR / "swebench_normal.json"
    assert fixture_path.exists()

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    adapter = get_adapter("swebench")
    assert adapter is not None
    assert isinstance(adapter, SWEBenchAdapter)

    # 1. Validation
    val_res = adapter.validate(raw_data)
    assert val_res.is_valid

    # 2. Parsing into RawTraceEvents
    events = adapter.parse(raw_data)
    assert len(events) == 5
    assert events[0].agent_id == "swe-agent-gpt4"
    assert events[0].raw_symbol == "search_code"
    assert events[2].raw_symbol == "edit_file"
    assert events[3].raw_symbol == "run_test"
    assert events[4].raw_symbol == "submit"
    assert events[4].event_type == "terminate"

    # 3. CES Emission and Schema Validation
    ces_records = [adapter.to_ces(e) for e in events]
    assert len(ces_records) == 5
    for r in ces_records:
        ces_dict = r.model_dump()
        v = validate_ces_record(ces_dict)
        assert v.is_valid, f"CES validation error: {v.errors}"
        assert r.framework == "swebench"
        assert r.attributes.param_schema_hash != ""


def test_osworld_adapter_fixture_contract():
    fixture_path = FIXTURES_DIR / "osworld_normal.json"
    assert fixture_path.exists()

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    adapter = get_adapter("osworld")
    assert adapter is not None
    assert isinstance(adapter, OSWorldAdapter)

    # 1. Validation
    val_res = adapter.validate(raw_data)
    assert val_res.is_valid

    # 2. Parsing
    events = adapter.parse(raw_data)
    assert len(events) == 5
    assert events[0].agent_id == "osworld-multimodal-agent"
    assert events[0].raw_symbol == "launch_application"
    assert events[1].raw_symbol == "mouse_click"
    assert events[2].raw_symbol == "keyboard_type"
    assert events[3].raw_symbol == "screen_capture"
    assert events[4].raw_symbol == "task_complete"
    assert events[4].event_type == "terminate"

    # 3. CES Emission
    ces_records = [adapter.to_ces(e) for e in events]
    assert len(ces_records) == 5
    for r in ces_records:
        ces_dict = r.model_dump()
        v = validate_ces_record(ces_dict)
        assert v.is_valid, f"CES validation error: {v.errors}"
        assert r.framework == "osworld"


def test_pipeline_benchmark_auto_detection(tmp_path):
    db_file = str(tmp_path / "auto_detect.sqlite")
    store = TraceStore(db_file)
    pipeline = IngestionPipeline(trace_store=store)

    swe_file = str(FIXTURES_DIR / "swebench_normal.json")
    res_swe = pipeline.ingest_file(swe_file)
    assert len(res_swe.accepted) == 5
    assert len(res_swe.rejected) == 0

    osworld_file = str(FIXTURES_DIR / "osworld_normal.json")
    res_os = pipeline.ingest_file(osworld_file)
    assert len(res_os.accepted) == 5
    assert len(res_os.rejected) == 0


def test_cli_ingest_benchmark_swebench_and_train(tmp_path):
    db_file = str(tmp_path / "swe_cli.sqlite")
    swe_file = str(FIXTURES_DIR / "swebench_normal.json")

    # Ingest and train
    ret = cmd_ingest_benchmark(
        path_str=swe_file,
        dataset="swebench",
        db_path=db_file,
        train=True,
    )
    assert ret == 0

    # Verify model was created and activated
    repo = ModelRepository(db_file)
    active = repo.get_active_model("swe-agent-gpt4")
    assert active is not None
    pdfa = active["pdfa"]
    assert len(pdfa.states) == 6
    assert "submit" in pdfa.alphabet


def test_cli_ingest_benchmark_osworld_and_train(tmp_path):
    db_file = str(tmp_path / "osworld_cli.sqlite")
    osworld_file = str(FIXTURES_DIR / "osworld_normal.json")

    ret = cmd_ingest_benchmark(
        path_str=osworld_file,
        dataset="osworld",
        db_path=db_file,
        train=True,
    )
    assert ret == 0

    repo = ModelRepository(db_file)
    active = repo.get_active_model("osworld-multimodal-agent")
    assert active is not None
    pdfa = active["pdfa"]
    assert len(pdfa.states) == 6
    assert "task_complete" in pdfa.alphabet
