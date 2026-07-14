"""pyslang-based parser — the primary RTL frontend.

pyslang (slang's Python bindings) is a full SystemVerilog 2017 front-end, so it
handles everything pyverilog chokes on:
  - parameter default values with comparison/arithmetic expressions
    (e.g. ``parameter X = (W>8)`` — pyverilog ParseErrors here)
  - non-ANSI module ports (direction/width declared in the body)
  - parameterized widths, function calls, conditionals, bit concatenation
  - cross-file parameter references (one Compilation holds every source file)

This is the primary path in port_resolver.resolve(). On any failure it raises
SlangParseError; the caller falls back to the per-file pyverilog+regex path.

Requires pyslang>=11.0.0 (prebuilt manylinux wheel — pip-installable, no compile).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


class SlangParseError(Exception):
    """Raised when pyslang cannot produce a clean parse of the sources."""


@dataclass
class SlangInfo:
    """Subset of ports/parameters pulled out of pyslang's semantic AST."""
    module_name: str = ""
    ports: list[dict] = field(default_factory=list)        # [{name, direction, width, sign}]
    parameters: list[dict] = field(default_factory=list)   # [{name, value, width, signed}]


# --- ConstantValue → int ----------------------------------------------------

def _cv_to_int(cv) -> int:
    """Convert a pyslang ConstantValue to a Python int.

    cv.value is an SVInt (or other concrete value); str() yields either a bare
    integer ("4096") or a sized literal ("1'b0", "8'hFF"). We reuse the existing
    regex_parser._eval_param_expr which already parses Verilog literals.
    """
    from .regex_parser import _eval_param_expr
    try:
        inner = cv.value  # SVInt / ScalarMap / etc.
        return _eval_param_expr(str(inner))
    except Exception:                                          # noqa: BLE001
        try:
            return _eval_param_expr(str(cv))
        except Exception:                                      # noqa: BLE001
            return 0


# --- direction mapping ------------------------------------------------------

def _direction_name(direction) -> str:
    """ArgumentDirection → 'input'/'output'/'inout'."""
    name = str(direction).rsplit(".", 1)[-1]  # 'ArgumentDirection.In' -> 'In'
    return {
        "In": "input",
        "Out": "output",
        "InOut": "inout",
        "Ref": "input",   # ref ports are rare; default to input
    }.get(name, "input")


# --- entry point ------------------------------------------------------------

def parse(files: list[str], top: str) -> SlangInfo:
    """Parse every source file in one Compilation, return top's ports+params.

    files: rtl directory's .v/.sv files (topologically sorted by `include).
    top: the top-level module name to extract — submodules are excluded.

    Raises SlangParseError if pyslang is unavailable, the top module is absent,
    or there are Error-severity parse diagnostics.
    """
    try:
        from pyslang.syntax import SyntaxTree
        from pyslang.ast import Compilation
    except ImportError as exc:
        raise SlangParseError(f"pyslang not installed: {exc}") from exc

    comp = Compilation()
    for f in files:
        src = Path(f).read_text(encoding="utf-8", errors="ignore")
        tree = SyntaxTree.fromText(src, Path(f).name)
        comp.addSyntaxTree(tree)

    # Treat Error/Fatal parse diagnostics as failure. Warnings are tolerated.
    errs = [d for d in comp.getParseDiagnostics()
            if str(getattr(d, "severity", "")).endswith(("Error", "Fatal"))]
    if errs:
        msgs = "; ".join(str(d) for d in errs[:5])
        raise SlangParseError(f"pyslang parse errors: {msgs}")

    # Find the top-level instance named `top`.
    root = comp.getRoot()
    inst = None
    for sym in root:
        if getattr(sym, "name", "") == top and type(sym).__name__ == "InstanceSymbol":
            inst = sym
            break
    if inst is None:
        # Maybe the module exists but isn't instantiated at top level; try by
        # definition lookup so an un-instantiated top module still parses.
        defs = getattr(root, "definitions", None)
        found = None
        if defs is not None:
            try:
                for d in defs:
                    if getattr(d, "name", "") == top:
                        found = d
                        break
            except Exception:                              # noqa: BLE001
                pass
        if found is not None:
            inst = found
        else:
            avail = [getattr(s, "name", "") for s in root
                     if type(s).__name__ == "InstanceSymbol"]
            raise SlangParseError(
                f"top module {top!r} not found in {len(files)} files; "
                f"available top instances: {avail}")

    info = SlangInfo(module_name=top)
    body = inst.body if inst is not None else None

    if body is not None and hasattr(body, "portList"):
        for p in body.portList:
            name = getattr(p, "name", "") or ""
            if not name:
                continue
            direction = _direction_name(getattr(p, "direction", ""))
            width = 1
            t = getattr(p, "type", None)
            if t is not None:
                try:
                    width = max(int(getattr(t, "bitWidth", 1) or 1), 1)
                except (TypeError, ValueError):
                    width = 1
            sign = "signed" if (t is not None and getattr(t, "isSigned", False)) else "unsigned"
            info.ports.append({
                "name": name, "direction": direction,
                "width": width, "sign": sign,
            })

    if body is not None and hasattr(body, "parameters"):
        for pm in body.parameters:
            name = getattr(pm, "name", "") or ""
            if not name:
                continue
            value = _cv_to_int(getattr(pm, "value", None))
            signed = False
            try:
                inner = getattr(getattr(pm, "value", None), "value", None)
                if inner is not None:
                    signed = bool(getattr(inner, "isSigned", False))
            except Exception:                              # noqa: BLE001
                pass
            width = 32
            try:
                if inner is not None:
                    width = max(int(getattr(inner, "bitWidth", 32) or 32), 1)
            except (TypeError, ValueError):
                width = 32
            info.parameters.append({
                "name": name, "value": value, "width": width, "signed": signed,
            })

    if not info.ports:
        raise SlangParseError(f"pyslang parsed {top!r} but extracted 0 ports")
    return info
