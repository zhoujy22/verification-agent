"""Verilog preprocessor: strip comments, expand `include, simplify ifdef.

This is intentionally minimal — full macro expansion is PyVerilog's job.
We only do enough pre-cleaning that:
  - the regex parser sees the same logical source a human would,
  - PyVerilog isn't tripped up by trailing backslashes on ``//``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Allow-list for `ifdef simplification. Anything else is left alone.
_IFDEF_ALLOWLIST = {"SIMULATION", "VERILATOR", "COCOTB_SIM", "DEBUG", "SYNTHESIS"}


def _strip_block_comments(text: str) -> str:
    # /* ... */ (non-nested, non-greedy)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _strip_line_comments(text: str) -> str:
    # // everything to EOL — but preserve `\` line continuation so port headers stay whole.
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            # skip until newline, but keep the newline
            j = text.find("\n", i)
            if j == -1:
                break
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _join_continued_lines(text: str) -> str:
    # `\` at end of line → join with next line
    return re.sub(r"\\\s*\n", " ", text)


def _expand_includes(text: str, include_dirs: Iterable[str], seen: set[str] | None = None) -> str:
    """Inline `include "foo.vh" by reading from include_dirs."""
    if seen is None:
        seen = set()

    def replacer(match: re.Match) -> str:
        path = match.group(1)
        target = None
        for d in include_dirs:
            p = Path(d) / path
            if p.exists():
                target = p.resolve()
                break
        if target is None:
            return match.group(0)  # leave untouched
        key = str(target)
        if key in seen:
            return ""  # guard against cycles
        seen.add(key)
        included = _expand_includes(target.read_text(encoding="utf-8", errors="ignore"), include_dirs, seen)
        return f"\n// >>> {path}\n{included}\n// <<< {path}\n"

    return re.sub(r'`\s*include\s+["<](\S+)[">]\s*', replacer, text)


def _simplify_ifdef(text: str) -> str:
    """Best-effort ifdef elimination: keep the defined branch only for allow-listed macros.

    We treat `ifndef X the same as `ifdef X` (negation) — keep the *other* branch.
    Anything we don't recognize is left in place; PyVerilog can still try.
    """
    lines = text.split("\n")
    out: list[str] = []
    depth = 0
    in_skip = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("`ifndef "):
            macro = stripped.split()[1]
            if macro in _IFDEF_ALLOWLIST:
                # the matching `elsif / `else branch — we'd need a stack; defer.
                depth += 1
                in_skip = True
                continue
        if stripped.startswith("`ifdef "):
            macro = stripped.split()[1]
            if macro in _IFDEF_ALLOWLIST:
                depth += 1
                in_skip = False
                continue
            # unknown macro: emit the line as-is (don't try to guess)
            out.append(line)
            continue
        if stripped.startswith("`endif"):
            if depth > 0:
                depth -= 1
                if depth == 0:
                    in_skip = False
            continue
        if depth == 0 or not in_skip:
            out.append(line)
    return "\n".join(out)


def preprocess(src: str, include_dirs: Iterable[str] = ()) -> str:
    """Clean a Verilog source string for downstream parsing."""
    text = src
    text = _strip_block_comments(text)
    text = _strip_line_comments(text)
    text = _join_continued_lines(text)
    text = _expand_includes(text, include_dirs)
    text = _simplify_ifdef(text)
    return text
