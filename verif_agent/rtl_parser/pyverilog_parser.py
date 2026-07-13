"""PyVerilog wrapper — header-only parsing.

PyVerilog 1.3.0's LALR parser chokes on common body constructs (e.g.
``reg [1:0] a = X, b;`` raises ``ParseError``), which kills the whole-file
parse and loses the port list with it. Since we only need the ports — which
live entirely in the module header ``module name #(...) (...);`` — we slice
each header, drop the body, and feed only headers to PyVerilog. Every
body-level syntax incompatibility is sidestepped.

On any failure ``parse`` raises ``ParseError``; the caller (port_resolver)
falls back to regex.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when PyVerilog (or any frontend) cannot handle the source."""


@dataclass
class PyVerilogInfo:
    """Subset of ports/parameters we pull out of PyVerilog's AST."""
    module_name: str = ""
    ports: list[dict] = field(default_factory=list)        # [{name, direction, width, sign}]
    parameters: list[dict] = field(default_factory=list)   # [{name, value, width, signed}]


# --- module-header extraction ----------------------------------------------

def _extract_headers(text: str) -> str:
    """Return ``module ... ;`` headers only, body stripped, one per module.

    Tracks parenthesis depth from each ``module`` keyword and cuts at the first
    depth-0 ``;`` (the header terminator). Comments are stripped first so the
    ``/* AXI slave interface */`` blocks common in port lists don't skew depth.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"\\\s*\n", " ", text)

    headers: list[str] = []
    for m in re.finditer(r"\bmodule\b\s+(\w+)", text):
        start, depth, k, n = m.start(), 0, m.start(), len(text)
        while k < n:
            c = text[k]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == ";" and depth == 0:
                headers.append(text[start:k + 1])
                break
            k += 1
    return "\n".join(h + "\nendmodule\n" for h in headers)


# --- expression evaluation -------------------------------------------------

def _vint(s: str) -> int:
    """Parse a Verilog integer literal: ``32``, ``8'hFF``, ``1'b0``, ``32'd2``."""
    s = (s or "0").strip()
    m = re.match(r"(\d+)?'([bBoOdDhH])([0-9a-fA-F_xXzZ]+)", s)
    if m:
        base = {'b': 2, 'o': 8, 'd': 10, 'h': 16}[m.group(2).lower()]
        digits = m.group(3).replace('_', '').replace('x', '0').replace('z', '0')
        return int(digits, base) if digits else 0
    try:
        return int(s.replace('_', ''))
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


_BINOPS = {
    "Minus":  lambda a, b: a - b,
    "Plus":   lambda a, b: a + b,
    "Times":  lambda a, b: a * b,
    "Divide": lambda a, b: a // b if b else 0,
    "Mod":    lambda a, b: a % b if b else 0,
    "Power":  lambda a, b: a ** b,
    "Sll":    lambda a, b: a << b,
    "Srl":    lambda a, b: a >> b,
}


def _eval(node, params: dict[str, int]) -> int:
    """Recursively evaluate a PyVerilog expression node to an int."""
    if node is None:
        return 0
    cn = type(node).__name__
    if cn == "IntConst":
        return _vint(getattr(node, "value", "0"))
    if cn == "FloatConst":
        try:
            return int(float(getattr(node, "value", "0")))
        except Exception:                                  # noqa: BLE001
            return 0
    if cn == "Rvalue":                                     # value wrapper
        kids = list(node.children())
        return _eval(kids[0], params) if kids else 0
    if cn == "Identifier":
        return params.get(str(getattr(node, "name", "")), 0)
    kids = list(node.children()) if hasattr(node, "children") else []
    if cn in _BINOPS and len(kids) >= 2:
        return _BINOPS[cn](_eval(kids[0], params), _eval(kids[1], params))
    if cn == "Uminus" and kids:
        return -_eval(kids[0], params)
    if cn == "Uplus" and kids:
        return _eval(kids[0], params)
    return 0


# --- AST extraction --------------------------------------------------------

def _params_of(module_def) -> dict[str, int]:
    """Ordered parameter table; later params may reference earlier ones."""
    params: dict[str, int] = {}
    pl = getattr(module_def, "paramlist", None)
    if not pl or not hasattr(pl, "params"):
        return params
    for decl in pl.params:                                 # Decl node
        items = decl.children() if hasattr(decl, "children") else [decl]
        for it in items:
            if type(it).__name__ not in ("Parameter", "Localparam"):
                continue
            name = str(getattr(it, "name", "") or "")
            if name:
                params[name] = _eval(getattr(it, "value", None), params)
    return params


def _ports_of(module_def, params: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    pl = getattr(module_def, "portlist", None)
    if not pl or not hasattr(pl, "ports"):
        return out
    for port in pl.ports:
        # ANSI ports: port is an Ioport whose .first is Input/Output/Inout.
        node = getattr(port, "first", None) or port
        name = str(getattr(node, "name", "") or "")
        if not name:
            continue
        direction = type(node).__name__.lower()
        if direction not in ("input", "output", "inout"):
            direction = "input"
        w = getattr(node, "width", None)
        width = 1
        if w is not None and getattr(w, "msb", None) is not None:
            width = max(_eval(w.msb, params) - _eval(w.lsb, params) + 1, 1)
        sign = "signed" if getattr(node, "signed", False) else "unsigned"
        out.append({"name": name, "direction": direction, "width": width, "sign": sign})
    return out


# --- entry point -----------------------------------------------------------

def parse(text: str, filename: str = "<unknown>", top: str | None = None) -> PyVerilogInfo:
    """Run PyVerilog on header-only source. Raises ParseError on failure.

    If `top` is given, only that module's ports/params are extracted — modules
    instantiated inside the file (submodules) are skipped, so multi-file designs
    don't pollute the top-level port list.
    """
    try:
        from pyverilog.vparser.parser import parse as pyv_parse   # type: ignore
    except ImportError as exc:
        raise ParseError(f"pyverilog not installed: {exc}") from exc

    headers = _extract_headers(text)
    if not headers.strip():
        raise ParseError(f"no module header found in {filename}")

    # Isolate LALR table generation in a temp dir (default '.' would write
    # parsetab.py / parser.out into the mounted work tree).
    tmp_dir = tempfile.mkdtemp(prefix="pyv_")
    tmp_path = str(Path(tmp_dir) / "src.v")
    try:
        Path(tmp_path).write_text(headers, encoding="utf-8")
        ast, _ = pyv_parse([tmp_path], outputdir=tmp_dir)
    except Exception as exc:                                # noqa: BLE001
        raise ParseError(f"pyverilog failed on {filename}: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    info = PyVerilogInfo()
    for d in getattr(ast.description, "definitions", []):
        if type(d).__name__ != "ModuleDef":
            continue
        d_name = str(getattr(d, "name", "") or "")
        if top and d_name != top:
            continue   # only the requested top module — skip instantiated submodules
        if not info.module_name:
            info.module_name = d_name
        params = _params_of(d)
        info.ports.extend(_ports_of(d, params))
        for nm, val in params.items():
            info.parameters.append({"name": nm, "value": val, "width": 32, "signed": False})

    if not info.ports:
        raise ParseError(f"no ports extracted from {filename}")
    return info
