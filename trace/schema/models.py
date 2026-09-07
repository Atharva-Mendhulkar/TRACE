"""
Canonical Event Schema (CES v1.0) Models and Validation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

import jsonschema
from pydantic import BaseModel, Field, field_validator

SCHEMA_PATH = Path(__file__).parent / "ces-v1.json"

EventType = Literal[
    "tool_call",
    "tool_result",
    "delegate",
    "retry",
    "memory_read",
    "memory_write",
    "plan_step",
    "error",
    "terminate",
]

EventStatus = Literal["success", "failure", "timeout", "unknown"]

FrameworkType = Literal[
    "mcp",
    "langgraph",
    "crewai",
    "openai_agents_sdk",
    "semantic_kernel",
    "google_adk",
    "autogen",
    "custom",
]

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|bearer)"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}"),
]


def redact_secrets_from_schema(schema_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Redact secret-shaped keys and values before parameter hashing (PRD §26.2)."""
    cleaned = {}
    for k, v in schema_dict.items():
        # Check key name
        is_secret_key = any(p.search(str(k)) for p in SECRET_PATTERNS)
        if is_secret_key:
            cleaned[k] = "[REDACTED_SECRET_KEY]"
        elif isinstance(v, dict):
            cleaned[k] = redact_secrets_from_schema(v)
        elif isinstance(v, list):
            cleaned[k] = [
                redact_secrets_from_schema(x) if isinstance(x, dict) else x for x in v
            ]
        elif isinstance(v, str) and any(p.search(v) for p in SECRET_PATTERNS):
            cleaned[k] = "[REDACTED_SECRET_VAL]"
        else:
            cleaned[k] = v
    return cleaned


def compute_param_schema_hash(param_schema: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of parameter schema shape (PRD §10.6, §26.2)."""
    redacted = redact_secrets_from_schema(param_schema)
    canonical_json = json.dumps(redacted, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class EventAttributes(BaseModel):
    param_schema_hash: str = Field(description="SHA-256 hash of parameter schema shape")
    status: EventStatus = Field(default="unknown")
    latency_ms: Optional[int] = Field(default=None, ge=0)
    retry_count: Optional[int] = Field(default=None, ge=0)


class ErrorInfo(BaseModel):
    error_class: str = Field(
        description="Coarse, framework-independent error category"
    )
    retryable: bool = Field(default=False)


class ProvenanceInfo(BaseModel):
    timestamp_source: Literal["framework", "ingestion"] = "framework"
    ingested_at: Optional[str] = None


class CESRecord(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    span_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_span_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: str
    role: Optional[str] = None
    depth: int = Field(default=0, ge=0)
    event_type: EventType
    symbol: str
    raw_symbol: str
    attributes: EventAttributes
    error: Optional[ErrorInfo] = None
    timestamp: str
    framework: FrameworkType
    framework_schema_version: str
    adapter_version: str
    sequence_no: int = Field(ge=0)
    status: EventStatus = "unknown"
    provenance: ProvenanceInfo = Field(default_factory=ProvenanceInfo)

    @field_validator("event_id", "trace_id", "span_id", "parent_span_id", mode="before")
    @classmethod
    def validate_uuid_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)


class ValidationResult(BaseModel):
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    schema_version: str = "1.0"


class ViolationRecord(BaseModel):
    """Runtime violation record conforming to PRD §29."""

    violation_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    event_id: str
    agent_id: str
    role: Optional[str] = None
    classification: List[str] = Field(default_factory=list)
    explanation: Dict[str, Any] = Field(default_factory=dict)
    model_id: Optional[str] = None
    policy_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


_SCHEMA_CACHE: Optional[Dict[str, Any]] = None


def load_ces_schema() -> Dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def validate_ces_record(record_dict: Dict[str, Any]) -> ValidationResult:
    """Validate a raw record dictionary against the canonical JSON schema."""
    schema = load_ces_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for e in validator.iter_errors(record_dict):
        field_path = "/".join(str(p) for p in e.path)
        if field_path:
            errors.append(f"{field_path}: {e.message}")
        else:
            errors.append(e.message)
    return ValidationResult(is_valid=len(errors) == 0, errors=errors)
