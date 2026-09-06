"""
Policy DSL Lexer, Parser, and AST (PRD §18.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Union


@dataclass
class SymbolPattern:
    raw: str
    is_wildcard: bool = False
    prefix: str = ""

    @classmethod
    def parse(cls, expr: str) -> SymbolPattern:
        expr = expr.strip()
        m = re.match(r"tool_call\(([^)]+)\)", expr)
        if m:
            content = m.group(1).strip()
            if content == "*":
                return cls(raw="*", is_wildcard=True, prefix="")
            if content.endswith("*"):
                return cls(raw=content, is_wildcard=True, prefix=content[:-1])
            return cls(raw=content, is_wildcard=False, prefix=content)

        m_del = re.match(r"delegate\(([^)]+)\)", expr)
        if m_del:
            content = m_del.group(1).strip()
            if content == "*":
                return cls(raw="delegate(*)", is_wildcard=True, prefix="delegate(")
            if content.endswith("*"):
                return cls(raw=expr, is_wildcard=True, prefix=f"delegate({content[:-1]}")
            return cls(raw=expr, is_wildcard=False, prefix=expr)

        if expr.endswith("*"):
            return cls(raw=expr, is_wildcard=True, prefix=expr[:-1])
        return cls(raw=expr, is_wildcard=False, prefix=expr)

    def matches(self, symbol: str) -> bool:
        if self.is_wildcard:
            if not self.prefix:
                return True
            return symbol.startswith(self.prefix)
        return symbol == self.raw


@dataclass
class RequireBeforeRule:
    kind: Literal["require_before"] = "require_before"
    precondition: SymbolPattern = field(default_factory=lambda: SymbolPattern(""))
    trigger: SymbolPattern = field(default_factory=lambda: SymbolPattern(""))
    within_k: Optional[int] = None
    scope: Literal["TRACE", "DELEGATION"] = "TRACE"


@dataclass
class ForbidSequenceRule:
    kind: Literal["forbid_sequence"] = "forbid_sequence"
    sequence: List[SymbolPattern] = field(default_factory=list)
    within_k: Optional[int] = None
    scope: Literal["TRACE", "DELEGATION"] = "TRACE"


@dataclass
class RequireOrderRule:
    kind: Literal["require_order"] = "require_order"
    sequence: List[SymbolPattern] = field(default_factory=list)
    scope: Literal["TRACE", "DELEGATION"] = "TRACE"


@dataclass
class LimitRule:
    kind: Literal["limit"] = "limit"
    target: SymbolPattern = field(default_factory=lambda: SymbolPattern(""))
    max_count: int = 1
    scope: Literal["TRACE", "DELEGATION"] = "TRACE"


PolicyRule = Union[RequireBeforeRule, ForbidSequenceRule, RequireOrderRule, LimitRule]


@dataclass
class PolicyAST:
    name: str
    rules: List[PolicyRule] = field(default_factory=list)


class PolicyParser:
    """Parser for TRACE Policy DSL."""

    @classmethod
    def parse(cls, source: str) -> PolicyAST:
        lines = [line.strip() for line in source.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            raise ValueError("Empty policy source")

        first = lines[0]
        m = re.match(r"^POLICY\s+([a-zA-Z_][a-zA-Z0-9_]*)$", first)
        if not m:
            raise ValueError(f"Policy must start with 'POLICY <name>', got: {first}")

        name = m.group(1)
        rules: List[PolicyRule] = []

        i = 1
        while i < len(lines):
            line = lines[i]

            # 1. REQUIRE x BEFORE y
            if line.startswith("REQUIRE ") and " BEFORE " in line:
                m_req = re.match(r"^REQUIRE\s+(.+?)\s+BEFORE\s+(.+)$", line)
                if not m_req:
                    raise ValueError(f"Malformed REQUIRE BEFORE rule: {line}")
                precondition = SymbolPattern.parse(m_req.group(1))
                trigger = SymbolPattern.parse(m_req.group(2))
                within_k = None
                scope = "TRACE"

                # Check following lines for WITHIN / SCOPE
                while i + 1 < len(lines) and (lines[i + 1].startswith("WITHIN ") or lines[i + 1].startswith("SCOPE ")):
                    i += 1
                    mod_line = lines[i]
                    if mod_line.startswith("WITHIN "):
                        m_w = re.match(r"^WITHIN\s+(\d+)\s+EVENTS?$", mod_line)
                        if m_w:
                            within_k = int(m_w.group(1))
                    elif mod_line.startswith("SCOPE "):
                        scope = "DELEGATION" if "DELEGATION" in mod_line else "TRACE"

                rules.append(RequireBeforeRule(precondition=precondition, trigger=trigger, within_k=within_k, scope=scope))

            # 2. FORBID SEQUENCE [ ... ]
            elif line.startswith("FORBID SEQUENCE"):
                m_forbid = re.match(r"^FORBID SEQUENCE\s*\[(.*)\]$", line)
                if not m_forbid:
                    raise ValueError(f"Malformed FORBID SEQUENCE: {line}")
                items = [SymbolPattern.parse(p.strip()) for p in m_forbid.group(1).split(",") if p.strip()]
                within_k = None
                scope = "TRACE"

                while i + 1 < len(lines) and (lines[i + 1].startswith("WITHIN ") or lines[i + 1].startswith("SCOPE ")):
                    i += 1
                    mod_line = lines[i]
                    if mod_line.startswith("WITHIN "):
                        m_w = re.match(r"^WITHIN\s+(\d+)\s+EVENTS?$", mod_line)
                        if m_w:
                            within_k = int(m_w.group(1))
                    elif mod_line.startswith("SCOPE "):
                        scope = "DELEGATION" if "DELEGATION" in mod_line else "TRACE"

                rules.append(ForbidSequenceRule(sequence=items, within_k=within_k, scope=scope))

            # 3. REQUIRE ORDER [ ... ]
            elif line.startswith("REQUIRE ORDER"):
                m_ord = re.match(r"^REQUIRE ORDER\s*\[(.*)\]$", line)
                if not m_ord:
                    raise ValueError(f"Malformed REQUIRE ORDER: {line}")
                items = [SymbolPattern.parse(p.strip()) for p in m_ord.group(1).split(",") if p.strip()]
                rules.append(RequireOrderRule(sequence=items))

            # 4. LIMIT x TO k PER TRACE|DELEGATION
            elif line.startswith("LIMIT "):
                m_lim = re.match(r"^LIMIT\s+(.+?)\s+TO\s+(\d+)\s+PER\s+(TRACE|DELEGATION)$", line)
                if not m_lim:
                    raise ValueError(f"Malformed LIMIT rule: {line}")
                target = SymbolPattern.parse(m_lim.group(1))
                max_cnt = int(m_lim.group(2))
                scope = m_lim.group(3)
                rules.append(LimitRule(target=target, max_count=max_cnt, scope=scope))  # type: ignore

            else:
                raise ValueError(f"Unrecognized policy rule syntax: {line}")

            i += 1

        return PolicyAST(name=name, rules=rules)
