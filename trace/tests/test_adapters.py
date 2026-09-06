"""
Contract Tests for Framework Adapters (PRD §11.5, §38).
"""

from pathlib import Path
import pytest

from trace.adapters.base import get_adapter, list_adapters
from trace.ingestion.pipeline import IngestionPipeline
from trace.schema.models import validate_ces_record

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_adapter_registry():
    adapters = list_adapters()
    fws = [a["framework"] for a in adapters]
    assert "mcp" in fws
    assert "langgraph" in fws


def test_mcp_adapter_fixture_contract():
    fixture_file = FIXTURES_DIR / "mcp_normal.json"
    pipeline = IngestionPipeline()
    result = pipeline.ingest_file(fixture_file, framework="mcp")

    assert len(result.rejected) == 0, f"Rejected events: {result.rejected}"
    assert len(result.accepted) >= 3

    # Verify each accepted event conforms 100% to CES JSON Schema
    for rec in result.accepted:
        val = validate_ces_record(rec.model_dump())
        assert val.is_valid, f"Record failed schema validation: {val.errors}"


def test_langgraph_adapter_fixture_contract():
    fixture_file = FIXTURES_DIR / "langgraph_normal.json"
    pipeline = IngestionPipeline()
    result = pipeline.ingest_file(fixture_file, framework="langgraph")

    assert len(result.rejected) == 0, f"Rejected events: {result.rejected}"
    assert len(result.accepted) >= 3

    for rec in result.accepted:
        val = validate_ces_record(rec.model_dump())
        assert val.is_valid, f"Record failed schema validation: {val.errors}"
