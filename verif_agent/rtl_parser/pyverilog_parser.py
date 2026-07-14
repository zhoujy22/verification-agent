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

# non-ANSI port declaration in the body: `input wire [W-1:0] foo, bar;`
# Captures the whole statement up to the first `;` so width/sign/type come along.
_BODY_PORT_DECL_RE = re.compile(
    r"\b(?P<dir>input|output|inout)\b"
    r"(?:\s+(?P<sign>signed|unsigned))?"
    r"(?:\s+(?P<type>wire|reg|logic))?"
    r"[^;]*?;",
    re.DOTALL,
)


def _extract_headers(text: str) -> str:
    """Return module headers + non-ANSI body port declarations, body otherwise stripped.

    For ANSI modules the header alone carries direction/width/sign. For
    non-ANSI modules the header lists only bare names; the direction/width live
    in body lines like ``input wire [7:0] data;``. We extract those body lines
    too (they are syntactically simple and don't trigger PyVerilog's body-level
    ParseError), so non-ANSI designs keep their port types.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"\\\s*\n", " ", text)

    out_parts: list[str] = []
    for m in re.finditer(r"\bmodule\b\s+(\w+)", text):
        start, depth, k, n = m.start(), 0, m.start(), len(text)
        header_end = None
        # Phase 1: find header terminator (first depth-0 `;`)
        while k < n:
            c = text[k]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == ";" and depth == 0:
                header_end = k + 1
                break
            k += 1
        if header_end is None:
            continue
        header = text[start:header_end]

        # Phase 2: scan body until endmodule, collect port declarations
        body_start = header_end
        endmod = text.find("endmodule", body_start)
        body_end = endmod if endmod != -1 else n
        body = text[body_start:body_end]
        body_decl_lines = [m2.group(0).strip()
                           for m2 in _BODY_PORT_DECL_RE.finditer(body)]

        part = header
        if body_decl_lines:
            part += "\n" + "\n".join(body_decl_lines)
        out_parts.append(part + "\nendmodule\n")

    return "\n".join(out_parts)


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


def _body_port_decls(module_def) -> dict[str, tuple[str, int, str]]:
    """Collect non-ANSI port declarations from the module body.

    For non-ANSI modules the header lists only bare names; direction/width/sign
    live in body Decl nodes (Input/Output/Inout). This returns a map
    name → (direction, width, sign) for those, so _ports_of can backfill
    header ports that have no type info.

    Only top-level Decl children of the ModuleDef are scanned (these are the
    module's own port declarations); nested function/task bodies are skipped.
    """
    decls: dict[str, tuple[str, int, str]] = {}
    for child in (module_def.children() if hasattr(module_def, "children") else []):
        if type(child).__name__ != "Decl":
            continue
        for item in (child.children() if hasattr(child, "children") else [child]):
            cn = type(item).__name__.lower()
            if cn not in ("input", "output", "inout"):
                continue
            nm = str(getattr(item, "name", "") or "")
            if not nm:
                continue
            # a Decl can declare multiple names sharing one type, but pyverilog
            # splits them into separate Input/Output nodes, so name is scalar here
            w = getattr(item, "width", None)
            width = 1
            if w is not None and getattr(w, "msb", None) is not None:
                width = max(_eval(w.msb, {}) - _eval(w.lsb, {}) + 1, 1)
            sign = "signed" if getattr(item, "signed", False) else "unsigned"
            decls[nm] = (cn, width, sign)
    return decls


def _ports_of(module_def, params: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    pl = getattr(module_def, "portlist", None)
    if not pl or not hasattr(pl, "ports"):
        return out
    body_decls = _body_port_decls(module_def)
    for port in pl.ports:
        # ANSI ports: port is an Ioport whose .first is Input/Output/Inout.
        node = getattr(port, "first", None) or port
        name = str(getattr(node, "name", "") or "")
        if not name:
            continue
        direction = type(node).__name__.lower()
        w = getattr(node, "width", None)
        width = 1
        sign = "signed" if getattr(node, "signed", False) else "unsigned"
        if w is not None and getattr(w, "msb", None) is not None:
            # ANSI: header carries full type
            width = max(_eval(w.msb, params) - _eval(w.lsb, params) + 1, 1)
        else:
            # non-ANSI: header has bare name — backfill direction/width/sign
            # from body Decl if present
            bd = body_decls.get(name)
            if bd:
                if direction not in ("input", "output", "inout"):
                    direction = bd[0]
                width = bd[1]
                sign = bd[2]
        if direction not in ("input", "output", "inout"):
            direction = "input"
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
