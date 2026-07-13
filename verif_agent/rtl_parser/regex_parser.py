"""Regex-based Verilog port/parameter/clock/reset extractor.

This is the **fallback** path — it must succeed on any sane Verilog so that
the design.json port list is never empty. PyVerilog augments type/signedness
when it succeeds; regex always fills in direction and width.

Supports both:
  - ANSI-style: `module foo #(params) (input wire [W-1:0] p1, output reg p2);`
  - non-ANSI:    `module foo (p1, p2); input [W-1:0] p1; output reg p2; endmodule`
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .preprocess import preprocess


# --- module header ---------------------------------------------------------

# `module <name> #(<param-list>) (<port-list-or-empty>);`
_MODULE_HDR_RE = re.compile(
    r"module\s+(?P<name>\w+)"
    r"(?P<params>\s*\#\s*\([^\)]*\))?"
    r"\s*(?P<ports>\([^;]*?\))?"
    r"\s*;",
    re.DOTALL,
)


# --- a single ANSI port declaration: `input wire [W-1:0] name, name2` ----
# Note: width bracket allows any Verilog expression (e.g. ADDR_WIDTH-1:0),
# not just literal digits. We capture hi/lo only when both sides are digits
# for later integer width calculation; otherwise the consumer falls back to
# port-level width lookups from parameters.
_ANSI_PORT_RE = re.compile(
    r"(?P<dir>input|output|inout)"
    r"(?:\s+(?P<sign>signed|unsigned))?"
    r"(?:\s+(?P<type>wire|reg|logic))?"
    r"(?:\s*\[\s*(?P<hi>\d+)\s*:\s*(?P<lo>\d+)\s*\])?"   # digit-only width; skip if expr
    r"(?:\s*\[\s*[^\]]*\])?"                            # parameterized width [..:..]
    r"\s+(?P<names>[\w][\w]*[,\s]?)+"
    r"(?=,|input|output|inout|$|\s*;)",
    re.DOTALL,
)


# --- a non-ANSI port declaration in body: `input [W-1:0] foo;` -----------
_BODY_PORT_RE = re.compile(
    r"^\s*(?P<dir>input|output|inout)"
    r"(?:\s+(?P<sign>signed|unsigned))?"
    r"(?:\s+(?P<type>wire|reg|logic))?"
    r"(?:\s*\[\s*(?P<hi>\d+)\s*:\s*(?P<lo>\d+)\s*\])?"   # digit-only width
    r"(?:\s*\[\s*([^\]]+)\s*\])?"                          # parameterized width
    r"\s+(?P<name>\w+)\s*$",
    re.DOTALL | re.MULTILINE,
)


# --- parameter list inside `#(...)` ----------------------------------------
_PARAM_RE = re.compile(
    r"(?P<type>parameter|localparam)?"
    r"\s*(?P<sign>signed|unsigned)?"
    r"\s*(?:\[(?P<hi>\d+):(?P<lo>\d+)\])?"
    r"\s*(?P<name>\w+)"
    r"\s*=\s*(?P<expr>[^,\)]+)"
)

# --- bare port list inside module foo (a, b, c) for non-ANSI -------------
_PORT_LIST_RE = re.compile(r"module\s+\w+\s*(?:\#\s*\([^\)]*\))?\s*\(([^;]*?)\)")


# --- input direction detection in `module foo (input a, output b)` ANSI ---
_ANSILIST_HDR_RE = re.compile(
    r"(input|output|inout)\s*(?:wire|reg|logic|signed|unsigned|\[\s*\d+\s*:\s*\d+\s*\]|\s+)*\s*(\w+)"
)


@dataclass
class ModuleInfo:
    """Raw port + parameter info from one file (before protocol classification)."""
    name: str
    direction: str          # "input" / "output" / "inout" / "unknown"
    width: int              # 1 for scalar; computed from `width_expr` post-merge
    sign: str               # "signed" / "unsigned"
    param: bool = False     # True if this row is a parameter (not a port)
    param_value: str | None = None
    width_expr: str | None = None  # raw text e.g. "ADDR_WIDTH-1:0" before resolution


@dataclass
class ParsedFile:
    """Per-file parse result."""
    filename: str
    module_name: str = ""
    items: list[ModuleInfo] = field(default_factory=list)


def _eval_param_expr(expr: str) -> int:
    """Evaluate a small integer expression — fall back to 0 on anything weird."""
    expr = expr.strip().rstrip(",")
    try:
        # Replace Verilog literals like 32'd12 / 16'h34 with Python ints.
        cleaned = re.sub(r"\d+\s*'\s*[dDhHoObB]\s*([0-9a-fA-Fx]+)", r"0x\1", expr)
        cleaned = re.sub(r"[^0-9xXa-fA-F\+\-\*\/\(\)\s]", "0", cleaned)
        # Only attempt eval if it looks like a numeric expression.
        if any(ch.isdigit() for ch in cleaned):
            return int(eval(cleaned, {"__builtins__": {}}, {}))   # noqa: S307 — controlled input
    except Exception:
        pass
    return 0


def _parse_param_block(params_text: str) -> list[ModuleInfo]:
    out: list[ModuleInfo] = []
    for m in _PARAM_RE.finditer(params_text):
        name = m.group("name")
        expr = m.group("expr") or ""
        width = 1
        if m.group("hi") and m.group("lo"):
            width = int(m.group("hi")) - int(m.group("lo")) + 1
        out.append(ModuleInfo(
            name=name, direction="input", width=max(width, 1),
            sign="signed" if m.group("sign") == "signed" else "unsigned",
            param=True, param_value=expr,
        ))
    return out


def _parse_module_body(body: str) -> tuple[list[ModuleInfo], list[ModuleInfo]]:
    """Parse non-ANSI: returns (params_in_body, ports_in_body)."""
    ports: list[ModuleInfo] = []
    params: list[ModuleInfo] = []
    for line in body.split("\n"):
        line = line.strip().rstrip(";").strip()
        if not line:
            continue
        # skip keywords we don't care about
        first_word = line.split()[0] if line else ""
        if first_word in {
            "wire", "reg", "logic", "assign", "always", "initial",
            "if", "case", "for", "while", "begin", "end", "module",
            "function", "task", "else", "integer",
        }:
            continue
        # explicit parameter declarations
        if line.startswith(("parameter", "localparam")):
            params.extend(_parse_param_block(line))
            continue
        # non-ANSI port-decl line: `input [W-1:0] foo`
        m = _BODY_PORT_RE.match(line)
        if m:
            port_name = m.group("name")
            width = 1
            width_expr: str | None = None
            if m.group("hi") and m.group("lo"):
                width = int(m.group("hi")) - int(m.group("lo")) + 1
            else:
                # parameter group is index 6 (digit-only group is 4+5)
                pg = m.group(6)
                if pg and any(ch.isalpha() for ch in pg):
                    width_expr = pg
            ports.append(ModuleInfo(
                name=port_name,
                direction=m.group("dir"),
                width=max(width, 1) if width_expr is None else 1,
                sign="signed" if m.group("sign") == "signed" else "unsigned",
                width_expr=width_expr,
            ))
    return ports, params


def _parse_ansi_portlist(portlist: str) -> list[ModuleInfo]:
    """Parse the (input a, output [3:0] b) header of an ANSI-style module."""
    items: list[ModuleInfo] = []
    # First: parameters (if they appear in the portlist). Verilog allows #() *before* ().
    # Then tokenize commas at the top level.
    depth = 0
    buf = ""
    parts: list[str] = []
    for ch in portlist:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)

    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = _ANSI_PORT_RE.match(p + ",")  # ensure terminator
        if not m:
            # Could be a parameter decl embedded (rare); skip silently.
            continue
        base_dir = m.group("dir")
        base_sign = m.group("sign") or "unsigned"
        width = 1
        if m.group("hi") and m.group("lo"):
            width = int(m.group("hi")) - int(m.group("lo")) + 1
        names_blob = (m.group("names") or "").strip()
        # If width captured was digit-only we already have the int. Otherwise we
        # store the raw bracket expression so port_resolver can resolve from params.
        width_expr: str | None = None
        if m.group("hi") is None and m.group("lo") is None:
            # The alternation `[^\]]*` will match parameterized widths. Try to
            # extract via the second-pass regex below.
            bb = re.search(r"\[\s*([^\]]+?)\s*\]", m.group(0))
            if bb and any(ch.isalpha() for ch in bb.group(1)):
                width_expr = bb.group(1)
        for nm in re.split(r"[,\s]+", names_blob):
            nm = nm.strip().rstrip(",;")
            if not nm:
                continue
            items.append(ModuleInfo(
                name=nm, direction=base_dir,
                width=max(width, 1) if width_expr is None else 1,
                sign=base_sign,
                width_expr=width_expr,
            ))
    return items


def _parse_nonansi_portnames(portlist: str) -> list[str]:
    """Pull the bare-name list like `(a, b, c)` for non-ANSI headers."""
    names = []
    for p in portlist.split(","):
        n = p.strip()
        if n:
            names.append(n)
    return names


def parse(text: str, filename: str = "<unknown>", top: str | None = None) -> ParsedFile:
    """Parse one Verilog source string. Always returns *something* — empty fields are OK.

    If `top` is given, only that module's header is parsed (submodules skipped).
    """
    src = preprocess(text)
    parsed = ParsedFile(filename=filename)
    hdr = None
    for m in _MODULE_HDR_RE.finditer(src):
        if top is None or m.group("name") == top:
            hdr = m
            break
    if not hdr:
        return parsed
    parsed.module_name = hdr.group("name")

    # 1) Header parameter block
    if hdr.group("params"):
        params_text = hdr.group("params")
        # strip leading `#(` and trailing `)`
        params_text = params_text.lstrip()
        if params_text.startswith("#"):
            params_text = params_text[1:]
        params_text = params_text.strip()
        if params_text.startswith("("):
            params_text = params_text[1:]
        if params_text.endswith(")"):
            params_text = params_text[:-1]
        parsed.items.extend(_parse_param_block(params_text))

    # 2) Body: from `;` after header until matching `endmodule`
    semi = hdr.end()
    body_end = src.lower().find("endmodule", semi)
    body = src[semi:body_end] if body_end != -1 else src[semi:]

    portlist = hdr.group("ports") or ""
    portlist = portlist.strip()
    if portlist.startswith("("):
        portlist = portlist[1:]
    if portlist.endswith(")"):
        portlist = portlist[:-1]

    body_ports, body_params = _parse_module_body(body)
    parsed.items.extend(body_params)

    # Detect style:
    #   ANSI if the portlist contains "input/output/inout" keywords for any item
    #   else non-ANSI — names listed; body declares direction+width.
    ports_in_list = _parse_ansi_portlist(portlist) if "input" in portlist or "output" in portlist or "inout" in portlist else []
    if ports_in_list:
        parsed.items.extend(ports_in_list)
    else:
        # Non-ANSI: match each body port's name to a position in portlist
        bare_names = _parse_nonansi_portnames(portlist)
        name_to_body: dict[str, ModuleInfo] = {p.name: p for p in body_ports}
        # also include any ports that the body declared but weren't in header list
        for nm in bare_names:
            if nm in name_to_body:
                parsed.items.append(name_to_body[nm])
        # Carry over any unnamed-but-declared body ports (best effort)
        for p in body_ports:
            if p.name not in bare_names:
                parsed.items.append(p)

    return parsed


def to_module_info_with_values(parsed: ParsedFile) -> list[ModuleInfo]:
    """Return items with parameters' values evaluated (best-effort)."""
    items: list[ModuleInfo] = []
    for item in parsed.items:
        if item.param and item.param_value is not None:
            item.width = max(item.width, 32)
        items.append(item)
    return items
