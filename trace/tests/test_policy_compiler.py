"""
Tests for Policy DSL & Compiler (PRD §18, §38).
"""

import pytest

from trace.policy.compiler import PolicyCompiler
from trace.policy.dsl import PolicyParser


def test_policy_parser_and_ast():
    source = """
    POLICY require_confirm_before_delete
      REQUIRE confirm_step BEFORE tool_call(delete_*)
      WITHIN 5 EVENTS
      SCOPE TRACE
    """
    ast = PolicyParser.parse(source)
    assert ast.name == "require_confirm_before_delete"
    assert len(ast.rules) == 1
    rule = ast.rules[0]
    assert rule.kind == "require_before"
    assert rule.precondition.raw == "confirm_step"
    assert rule.trigger.raw == "delete_*"
    assert rule.trigger.is_wildcard
    assert rule.within_k == 5


def test_compile_forbid_sequence():
    source = """
    POLICY forbid_delete_chain
      FORBID SEQUENCE [ delegate(reviewer), tool_call(delete_*) ]
    """
    ast = PolicyParser.parse(source)
    dfa = PolicyCompiler.compile(ast)

    # Sequence of [delegate(reviewer), delete_record] should lead to reject state
    s1 = dfa.step(dfa.q0, "delegate(reviewer)")
    assert s1 != dfa.reject_state

    s2 = dfa.step(s1, "delete_record")
    assert s2 == dfa.reject_state
    assert not dfa.is_accepted(s2)


def test_compile_require_before_infinite_scope():
    source = """
    POLICY safe_memory_write
      REQUIRE memory_read BEFORE memory_write
    """
    ast = PolicyParser.parse(source)
    dfa = PolicyCompiler.compile(ast)

    # Calling memory_write directly without memory_read should lead to reject!
    s_bad = dfa.step(dfa.q0, "memory_write")
    assert s_bad == dfa.reject_state

    # Calling memory_read first should allow memory_write
    s_good1 = dfa.step(dfa.q0, "memory_read")
    assert dfa.is_accepted(s_good1)
    s_good2 = dfa.step(s_good1, "memory_write")
    assert dfa.is_accepted(s_good2)


def test_compile_bounded_counter_within_k():
    source = """
    POLICY confirm_then_delete
      REQUIRE confirm_step BEFORE delete_record
      WITHIN 2 EVENTS
    """
    ast = PolicyParser.parse(source)
    dfa = PolicyCompiler.compile(ast)

    # Calling delete directly without confirm -> reject
    assert dfa.step(dfa.q0, "delete_record") == dfa.reject_state

    # Confirm step -> valid state
    s1 = dfa.step(dfa.q0, "confirm_step")
    assert dfa.is_accepted(s1)

    # Delete within 2 events -> valid
    s2 = dfa.step(s1, "delete_record")
    assert dfa.is_accepted(s2)
