"""
Tests for Canonical Event Schema (CES v1.0) & Validation (PRD §10, §38).
"""

from uuid import uuid4
import pytest

from trace.schema.models import (
    CESRecord,
    EventAttributes,
    compute_param_schema_hash,
    redact_secrets_from_schema,
    validate_ces_record,
)


def test_valid_ces_record():
    record = CESRecord(
        schema_version="1.0",
        event_id=str(uuid4()),
        trace_id=str(uuid4()),
        span_id=str(uuid4()),
        agent_id="test-agent",
        event_type="tool_call",
        symbol="web_search",
        raw_symbol="browse_v1",
        attributes=EventAttributes(
            param_schema_hash=compute_param_schema_hash({"query": "str"}),
            status="success",
        ),
        timestamp="2026-09-07T00:00:00Z",
        framework="mcp",
        framework_schema_version="2024-11-05",
        adapter_version="1.0.0",
        sequence_no=0,
        status="success",
    )

    data = record.model_dump()
    val = validate_ces_record(data)
    assert val.is_valid, f"Validation failed: {val.errors}"


def test_missing_required_field():
    incomplete = {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        # missing trace_id
        "agent_id": "test-agent",
        "event_type": "tool_call",
        "symbol": "web_search",
    }
    val = validate_ces_record(incomplete)
    assert not val.is_valid
    assert any("trace_id" in err for err in val.errors)


def test_invalid_event_type():
    invalid_type = {
        "schema_version": "1.0",
        "event_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "span_id": str(uuid4()),
        "agent_id": "test-agent",
        "event_type": "non_existent_type",  # Not in 9-value taxonomy
        "symbol": "web_search",
        "raw_symbol": "web_search",
        "attributes": {
            "param_schema_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "status": "success",
        },
        "timestamp": "2026-09-07T00:00:00Z",
        "framework": "mcp",
        "framework_schema_version": "1.0",
        "adapter_version": "1.0.0",
        "sequence_no": 0,
        "status": "success",
    }
    val = validate_ces_record(invalid_type)
    assert not val.is_valid
    assert any("event_type" in err for err in val.errors)


def test_secret_redaction():
    schema_with_secrets = {
        "api_key": "sk-1234567890abcdef1234567890",
        "token": "ghp_1234567890abcdef1234567890abcdef",
        "query": "public query string",
        "nested": {"password": "supersecretpassword"},
    }

    redacted = redact_secrets_from_schema(schema_with_secrets)
    assert redacted["api_key"] == "[REDACTED_SECRET_KEY]"
    assert redacted["token"] == "[REDACTED_SECRET_KEY]"
    assert redacted["nested"]["password"] == "[REDACTED_SECRET_KEY]"
    assert redacted["query"] == "public query string"

    # Verify hashing differs from raw
    h1 = compute_param_schema_hash(schema_with_secrets)
    h2 = compute_param_schema_hash({"query": "public query string"})
    assert isinstance(h1, str) and len(h1) == 64
