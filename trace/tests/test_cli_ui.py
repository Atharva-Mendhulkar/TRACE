"""
Unit Tests for TRACE Semantic CLI UI Engine and Main Dispatcher.
"""

import sys
from io import StringIO
import pytest

from trace.cli.ui import (
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
    strip_ansi,
)
from trace.cli.main import main


def test_cli_banner_content():
    rendered = cli_banner(version="1.0.0", print_out=False)
    plain = strip_ansi(rendered)
    assert "Trace-based Runtime Automata for Compliance & Enforcement" in plain
    assert "v1.0.0" in plain
    assert "╱$$" in plain
    assert "│ $$" in plain


def test_cli_alerts():
    s = cli_alert_success("Task complete", print_out=False)
    assert "✔" in s
    assert "Task complete" in s

    i = cli_alert_info("System status normal", print_out=False)
    assert "ℹ" in i
    assert "System status normal" in i

    w = cli_alert_warning("Warning check parameter", print_out=False)
    assert "▲" in w
    assert "Warning check parameter" in w

    d = cli_alert_danger("Critical error", print_out=False)
    assert "✖" in d
    assert "Critical error" in d

    a = cli_alert("Generic message", print_out=False)
    assert "•" in a
    assert "Generic message" in a


def test_cli_headings_and_boxes():
    h1 = cli_h1("Main Header", print_out=False)
    assert "MAIN HEADER" in h1

    h2 = cli_h2("Section Subheader", print_out=False)
    assert "Section Subheader" in h2

    h3 = cli_h3("Step Detail", print_out=False)
    assert "Step Detail" in h3

    b = cli_box("METRIC SUMMARY", ["Item 1: 42", "Item 2: True"], print_out=False)
    plain_b = strip_ansi(b)
    assert "╭─ METRIC SUMMARY" in plain_b
    assert "Item 1: 42" in plain_b
    assert "╰" in plain_b


def test_cli_table():
    headers = ["Component", "Status", "Latency"]
    rows = [
        ["Verifier", "ACTIVE", "0.012 ms"],
        ["DriftDetector", "NORMAL", "0.045 ms"],
    ]
    t = cli_table(headers, rows, print_out=False)
    plain_t = strip_ansi(t)
    assert "Component" in plain_t
    assert "Verifier" in plain_t
    assert "0.012 ms" in plain_t
    assert "┌" in plain_t
    assert "└" in plain_t


def test_cli_kv_and_progress():
    kv_line = cli_kv("Database URL", "sqlite:///trace.db", print_out=False)
    assert "Database URL" in kv_line
    assert "sqlite:///trace.db" in kv_line

    prog = cli_progress_bar(5, 10, label="Evaluation")
    assert "Evaluation" in prog
    assert "50%" in prog
    assert "(5/10)" in prog


def test_cli_main_no_args(capsys):
    ret = main([])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Trace-based Runtime Automata for Compliance & Enforcement" in captured.out
    assert "usage: trace" in captured.out


def test_cli_main_adapter_list(capsys):
    ret = main(["adapter", "list"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Registered Framework Adapters" in captured.out
    assert "mcp" in captured.out
    assert "langgraph" in captured.out
    assert "swebench" in captured.out
    assert "osworld" in captured.out


def test_cli_main_demo(tmp_path, capsys):
    db_file = str(tmp_path / "test_demo.sqlite")
    ret = main(["demo", "--delay", "0.0", "--db", db_file])
    assert ret == 0
    captured = capsys.readouterr()
    assert "TRACE COMPLETE CAPABILITIES DEMONSTRATION" in captured.out
    assert "DEMO COMPLETE" in captured.out


def test_cli_interactive_shell_exit(monkeypatch, capsys):
    from trace.cli.main import interactive_shell, build_parser
    parser = build_parser()
    
    # Simulate user typing "adapter list" then "exit"
    simulated_inputs = iter(["adapter list", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(simulated_inputs))

    ret = interactive_shell(parser)
    assert ret == 0
    captured = capsys.readouterr()
    assert "TRACE INTERACTIVE SESSION" in captured.out
    assert "Registered Framework Adapters" in captured.out
    assert "Exited TRACE environment" in captured.out

