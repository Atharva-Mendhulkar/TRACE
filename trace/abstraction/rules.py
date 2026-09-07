"""
Rule-based Semantic Abstraction (PRD §12.2, Phase 1).
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

# Deterministic synonym mapping rules
SYNONYM_RULES: Dict[str, str] = {
    "browse": "web_search",
    "search": "web_search",
    "search_web": "web_search",
    "google_search": "web_search",
    "bing_search": "web_search",
    "web_browser": "web_search",
    
    "read_file": "file_read",
    "view_file": "file_read",
    "open_file": "file_read",
    "get_file": "file_read",
    
    "write_file": "file_write",
    "save_file": "file_write",
    "create_file": "file_write",
    
    "delete_file": "file_delete",
    "remove_file": "file_delete",
    "delete_record": "delete_record",
    "remove_record": "delete_record",
    "drop_record": "delete_record",
    
    "run_command": "execute_code",
    "exec": "execute_code",
    "bash": "execute_code",
    "shell_exec": "execute_code",
    "terminal": "execute_code",
    
    "confirm": "confirm_step",
    "user_confirm": "confirm_step",
    "approval": "confirm_step",
    "ask_user": "confirm_step",
}


class RuleBasedAbstraction:
    """Deterministic, rule-based symbol canonicalization for Phase 1."""

    def __init__(self, custom_rules: Dict[str, str] | None = None):
        self.rules = dict(SYNONYM_RULES)
        if custom_rules:
            self.rules.update(custom_rules)

    def canonicalize(self, raw_symbol: str, event_type: str = "tool_call") -> str:
        """Map raw action name to canonical symbol."""
        cleaned = raw_symbol.strip().lower()

        # Check delegate patterns
        if event_type == "delegate":
            if cleaned.startswith("delegate(") and cleaned.endswith(")"):
                return cleaned
            return f"delegate({cleaned})"
        if cleaned.startswith("delegate(") and cleaned.endswith(")"):
            return cleaned

        # Direct rule lookup
        if cleaned in self.rules:
            return self.rules[cleaned]

        # Suffix matching for result/error
        if cleaned.endswith("_result"):
            base = cleaned[:-7]
            canonical_base = self.canonicalize(base, event_type)
            return f"{canonical_base}_result"

        # Regex normalization (remove version suffixes like _v1, _v2)
        v_stripped = re.sub(r"_v\d+$", "", cleaned)
        if v_stripped in self.rules:
            return self.rules[v_stripped]

        # If event type is fixed taxonomy
        if event_type in ("terminate", "retry", "memory_read", "memory_write", "plan_step"):
            if cleaned == event_type or not cleaned:
                return event_type

        return cleaned
