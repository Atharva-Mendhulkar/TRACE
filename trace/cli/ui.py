"""
TRACE Semantic CLI UI Engine.
Inspired by R/Posit cli and Python rich libraries.
Accent color: Electric Violet / Purple.
Provides semantic elements: banners, headings, alerts, rules, boxes, tables, and key-value cards.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ANSI Color & Style Sequences
class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # Standard colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Violet Accent Palette (User requested accent color: Violet)
    VIOLET = "\033[38;5;141m"        # Radiant medium violet
    BRIGHT_VIOLET = "\033[38;5;177m" # Vibrant light violet
    DEEP_VIOLET = "\033[38;5;99m"    # Deep rich violet
    PURPLE = "\033[38;5;135m"         # Neon purple
    LAVENDER = "\033[38;5;183m"       # Pale lavender
    DARK_VIOLET = "\033[38;5;55m"     # Border deep violet

    # Backgrounds
    BG_VIOLET = "\033[48;5;99m"
    BG_PURPLE = "\033[48;5;54m"
    BG_DARK_VIOLET = "\033[48;5;17m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_BLACK = "\033[40m"


def supports_color() -> bool:
    """Check if the current terminal supports ANSI color formatting."""
    if os.environ.get("NO_COLOR") or os.environ.get("TRACE_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term.lower() == "dumb":
        return False
    return True


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text to measure visual string length."""
    return _ANSI_ESCAPE_RE.sub("", text)


def style(text: str, *styles: str) -> str:
    """Apply ANSI styles if supported, else return raw text."""
    if not supports_color() or not styles:
        return text
    prefix = "".join(styles)
    return f"{prefix}{text}{Style.RESET}"


# Exact ASCII Banner requested for TRACE
ASCII_BANNER_LINES = [
    r"  ╱$$                                           ",
    r" │ $$                                           ",
    r"╱$$$$$$    ╱$$$$$$  ╱$$$$$$   ╱$$$$$$$  ╱$$$$$$ ",
    r"│_  $$_╱   ╱$$__  $$│____  $$ ╱$$_____╱ ╱$$__  $$",
    r"  │ $$    │ $$  ╲__╱ ╱$$$$$$$│ $$      │ $$$$$$$$",
    r"  │ $$ ╱$$│ $$      ╱$$__  $$│ $$      │ $$_____╱",
    r"  │  $$$$╱│ $$     │  $$$$$$$│  $$$$$$$│  $$$$$$$",
    r"   ╲___╱  │__╱      ╲_______╱ ╲_______╱ ╲_______╱",
]


def cli_banner(version: str = "1.0.0", print_out: bool = True) -> str:
    """Render the official TRACE violet/purple ASCII art banner and subtitle."""
    out: List[str] = []
    # Violet gradient across lines: Lavender -> Bright Violet -> Radiant Violet -> Purple -> Deep Violet
    gradients = [
        (Style.LAVENDER, Style.BOLD),
        (Style.LAVENDER, Style.BOLD),
        (Style.BRIGHT_VIOLET, Style.BOLD),
        (Style.BRIGHT_VIOLET,),
        (Style.VIOLET, Style.BOLD),
        (Style.VIOLET,),
        (Style.PURPLE, Style.BOLD),
        (Style.DEEP_VIOLET,),
    ]

    out.append("")
    for i, line in enumerate(ASCII_BANNER_LINES):
        color_styles = gradients[i % len(gradients)]
        out.append(style(line, *color_styles))

    tag = style(f" v{version} ", Style.BOLD, Style.BRIGHT_WHITE, Style.BG_VIOLET)
    title = style("Trace-based Runtime Automata for Compliance & Enforcement", Style.BOLD, Style.WHITE)
    subtitle = style("Formal Behavioral Inference & Temporal Policy Verification for AI Agents", Style.DIM, Style.LAVENDER)

    out.append(f"  {tag}  {title}")
    out.append(f"  {subtitle}")
    out.append("")

    rendered = "\n".join(out)
    if print_out:
        print(rendered)
    return rendered


# Semantic Alert Messages
def cli_alert_success(text: str, print_out: bool = True) -> str:
    """Success alert message with a bright green checkmark."""
    prefix = style("✔", Style.BOLD, Style.BRIGHT_GREEN)
    content = style(text, Style.GREEN)
    line = f"{prefix}  {content}"
    if print_out:
        print(line)
    return line


def cli_alert_info(text: str, print_out: bool = True) -> str:
    """Informational alert message with a radiant violet glyph."""
    prefix = style("ℹ", Style.BOLD, Style.BRIGHT_VIOLET)
    content = style(text, Style.WHITE)
    line = f"{prefix}  {content}"
    if print_out:
        print(line)
    return line


def cli_alert_warning(text: str, print_out: bool = True) -> str:
    """Warning alert message with a yellow warning glyph."""
    prefix = style("▲", Style.BOLD, Style.BRIGHT_YELLOW)
    content = style(text, Style.YELLOW)
    line = f"{prefix}  {content}"
    if print_out:
        print(line)
    return line


def cli_alert_danger(text: str, print_out: bool = True) -> str:
    """Danger / Error alert message with a red cross."""
    prefix = style("✖", Style.BOLD, Style.BRIGHT_RED)
    content = style(text, Style.BRIGHT_RED)
    line = f"{prefix}  {content}"
    if print_out:
        print(line, file=sys.stderr)
    return line


def cli_alert(text: str, print_out: bool = True) -> str:
    """Generic bulleted alert message with a violet bullet."""
    prefix = style("•", Style.BOLD, Style.VIOLET)
    line = f"{prefix}  {text}"
    if print_out:
        print(line)
    return line


# Semantic Headings with Violet Accent
def cli_h1(text: str, print_out: bool = True) -> str:
    """Level 1 heading: uppercase bold bright violet with double-line divider."""
    header = style(f"\n═══  {text.upper()}  ═══", Style.BOLD, Style.BRIGHT_VIOLET)
    if print_out:
        print(header)
    return header


def cli_h2(text: str, print_out: bool = True) -> str:
    """Level 2 heading: bold white with subtle violet underline bar."""
    title = style(f"\n── {text} ", Style.BOLD, Style.BRIGHT_VIOLET)
    fill_len = max(0, 70 - len(strip_ansi(title)))
    rule = style("─" * fill_len, Style.DIM, Style.DEEP_VIOLET)
    header = f"{title}{rule}"
    if print_out:
        print(header)
    return header


def cli_h3(text: str, print_out: bool = True) -> str:
    """Level 3 heading: bullet arrow heading with radiant violet pointer."""
    header = f"\n  {style('❯', Style.BOLD, Style.BRIGHT_VIOLET)} {style(text, Style.BOLD, Style.WHITE)}"
    if print_out:
        print(header)
    return header


def cli_rule(title: str = "", char: str = "─", width: int = 76, print_out: bool = True) -> str:
    """Horizontal divider line with optional violet title."""
    if not title:
        line = style(char * width, Style.DIM, Style.DEEP_VIOLET)
    else:
        styled_title = f" {title} "
        raw_len = len(title) + 2
        remaining = max(0, width - raw_len - 4)
        left = char * 3
        right = char * remaining
        line = f"{style(left, Style.DIM, Style.DEEP_VIOLET)}{style(styled_title, Style.BOLD, Style.VIOLET)}{style(right, Style.DIM, Style.DEEP_VIOLET)}"
    if print_out:
        print(line)
    return line


# Semantic Box / Panel with Violet Border
def cli_box(
    title: str,
    lines: Sequence[str],
    style_color: str = Style.VIOLET,
    width: Optional[int] = None,
    print_out: bool = True,
) -> str:
    """Render a clean rounded box with violet borders containing styled lines."""
    raw_lengths = [len(strip_ansi(l)) for l in lines]
    raw_lengths.append(len(title) + 4)
    content_width = max(raw_lengths) if raw_lengths else 40
    box_width = max(width or 74, content_width + 4)

    # Top border: ╭─ Title ───────╮
    title_part = f" {title} " if title else ""
    remaining_top = box_width - len(title_part) - 3
    top_border = f"{style('╭─', style_color)}{style(title_part, Style.BOLD, Style.WHITE)}{style('─' * max(0, remaining_top) + '╮', style_color)}"

    out = [top_border]
    for line in lines:
        raw_len = len(strip_ansi(line))
        pad = " " * max(0, box_width - raw_len - 4)
        border_char = style("│", style_color)
        out.append(f"{border_char}  {line}{pad}  {border_char}")

    # Bottom border: ╰──────────────╯
    bottom_border = style("╰" + "─" * (box_width) + "╯", style_color)
    out.append(bottom_border)

    result = "\n".join(out)
    if print_out:
        print(result)
    return result


# Semantic Key-Value Pairs
def cli_kv(key: str, value: Any, indent: int = 2, key_width: int = 18, print_out: bool = True) -> str:
    """Print an aligned key-value pair with violet keys."""
    indent_str = " " * indent
    k_styled = style(f"{key:<{key_width}}", Style.DIM, Style.VIOLET)
    sep = style(":", Style.DIM, Style.DEEP_VIOLET)
    v_styled = style(str(value), Style.BOLD, Style.WHITE)
    line = f"{indent_str}{k_styled} {sep}  {v_styled}"
    if print_out:
        print(line)
    return line


# Semantic Badges & Status Pills
def cli_badge(text: str, category: str = "info") -> str:
    """Generate a high-visibility badge pill."""
    cat = category.lower()
    if cat in ("success", "active", "passed", "pass"):
        return style(f" {text} ", Style.BOLD, Style.BRIGHT_WHITE, Style.BG_VIOLET)
    elif cat in ("warning", "candidate", "pending"):
        return style(f" {text} ", Style.BOLD, Style.BLACK, "\033[48;5;183m")
    elif cat in ("danger", "error", "failed", "rejected", "retired"):
        return style(f" {text} ", Style.BOLD, Style.BRIGHT_WHITE, "\033[41m")
    else:
        return style(f" {text} ", Style.BOLD, Style.BRIGHT_WHITE, Style.BG_PURPLE)


def status_pill(status: str) -> str:
    """Render a status string with appropriate violet/green colors and symbols."""
    s = status.upper()
    if s == "ACTIVE":
        return f"{style('●', Style.BRIGHT_GREEN)} {style('ACTIVE', Style.BOLD, Style.GREEN)}"
    elif s == "CANDIDATE":
        return f"{style('○', Style.BRIGHT_VIOLET)} {style('CANDIDATE', Style.BOLD, Style.VIOLET)}"
    elif s == "RETIRED":
        return f"{style('◌', Style.DIM, Style.WHITE)} {style('RETIRED', Style.DIM, Style.WHITE)}"
    elif s == "REJECTED":
        return f"{style('✕', Style.BRIGHT_RED)} {style('REJECTED', Style.BOLD, Style.RED)}"
    elif s == "PROMOTED":
        return f"{style('★', Style.BRIGHT_VIOLET)} {style('PROMOTED', Style.BOLD, Style.BRIGHT_VIOLET)}"
    return status


# Semantic Unicode Tables with Violet Borders
def cli_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    alignments: Optional[Sequence[str]] = None,
    print_out: bool = True,
) -> str:
    """Render an aligned Unicode box table with violet borders."""
    if not headers and not rows:
        return ""

    num_cols = len(headers)
    aligns = list(alignments) if alignments else ["left"] * num_cols
    while len(aligns) < num_cols:
        aligns.append("left")

    # Determine column widths
    col_widths = [len(strip_ansi(h)) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            if idx < num_cols:
                col_widths[idx] = max(col_widths[idx], len(strip_ansi(str(cell))))

    # Add 2 padding chars to each col
    col_widths = [w + 2 for w in col_widths]

    # Border definitions
    t_top = "┌" + "┬".join("─" * w for w in col_widths) + "┐"
    t_mid = "├" + "┼".join("─" * w for w in col_widths) + "┤"
    t_bot = "└" + "┴".join("─" * w for w in col_widths) + "┘"

    out = [style(t_top, Style.VIOLET)]

    # Format header row
    hdr_cells = []
    for idx, h in enumerate(headers):
        w = col_widths[idx]
        text_len = len(strip_ansi(h))
        cell_str = f" {h}{' ' * (w - text_len - 1)}"
        hdr_cells.append(style(cell_str, Style.BOLD, Style.BRIGHT_WHITE))

    pipe = style("│", Style.VIOLET)
    out.append(f"{pipe}{pipe.join(hdr_cells)}{pipe}")
    out.append(style(t_mid, Style.VIOLET))

    # Format rows
    for row in rows:
        row_cells = []
        for idx in range(num_cols):
            w = col_widths[idx]
            raw_val = str(row[idx]) if idx < len(row) else ""
            val_len = len(strip_ansi(raw_val))
            align = aligns[idx].lower()

            if align == "right":
                pad_left = max(0, w - val_len - 1)
                cell_str = f"{' ' * pad_left}{raw_val} "
            elif align == "center":
                total_pad = max(0, w - val_len)
                pad_left = total_pad // 2
                pad_right = total_pad - pad_left
                cell_str = f"{' ' * pad_left}{raw_val}{' ' * pad_right}"
            else:
                pad_right = max(0, w - val_len - 1)
                cell_str = f" {raw_val}{' ' * pad_right}"

            row_cells.append(cell_str)

        out.append(f"{pipe}{pipe.join(row_cells)}{pipe}")

    out.append(style(t_bot, Style.VIOLET))

    rendered = "\n".join(out)
    if print_out:
        print(rendered)
    return rendered


# Semantic Progress Bar with Violet Fill
def cli_progress_bar(current: int, total: int, width: int = 28, label: str = "") -> str:
    """Render a progress bar string with electric violet progress."""
    if total <= 0:
        pct = 1.0
    else:
        pct = min(1.0, max(0.0, current / total))

    filled = int(round(width * pct))
    unfilled = width - filled
    bar = style("━" * filled, Style.BOLD, Style.BRIGHT_VIOLET) + style("─" * unfilled, Style.DIM, Style.DEEP_VIOLET)
    pct_text = style(f"{int(pct * 100):>3}%", Style.BOLD, Style.WHITE)
    counts = style(f"({current}/{total})", Style.DIM, Style.LAVENDER)
    lbl = f"{style(label, Style.BOLD, Style.WHITE)}  " if label else ""
    return f"{lbl}[{bar}] {pct_text} {counts}"
