"""Combine PyVerilog + regex sources into a single Design.

Top-level pipeline:
  1. discover all .v/.sv/.vh/.svh files under --rtl
  2. for each file: preprocess → try PyVerilog → fall back to regex
  3. merge port/param dicts (PyVerilog wins for sign/signedness; regex always
     fills direction/width when PyVerilog is silent)
  4. topological-sort compile_order using `include relations
  5. emit Design object
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..design import Clock, Design, Parameter, Reset, Port
from .preprocess import preprocess
from .pyverilog_parser import ParseError as PyvParseError
from .pyverilog_parser import parse as pyv_parse
from .regex_parser import ModuleInfo, ParsedFile, parse as rx_parse, to_module_info_with_values

log = logging.getLogger(__name__)

_RTL_EXTENSIONS = (".v", ".sv", ".vh", ".svh", ".verilog")


def discover_rtl_files(rtl_dir: str | Path) -> list[str]:
    """Return absolute paths to all RTL files, sorted by filename for stability."""
    rtl_path = Path(rtl_dir)
    if not rtl_path.exists():
        raise FileNotFoundError(f"--rtl directory not found: {rtl_dir}")
    files: list[str] = []
    for ext in _RTL_EXTENSIONS:
        files.extend(str(p.resolve()) for p in sorted(rtl_path.rglob(f"*{ext}")))
    # Stable, de-duplicated order
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _topological_sort(files: list[str]) -> list[str]:
    """Sort so that `included` files come before the includer (best-effort)."""
    deps: dict[str, set[str]] = {f: set() for f in files}
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"`\s*include\s+[\"<](\S+)[\">]", text):
            name = m.group(1)
            # match a same-tree filename
            for other in files:
                if other != f and Path(other).name == name:
                    deps[f].add(other)
    visited: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for d in sorted(deps.get(node, set())):
            visit(d)
        order.append(node)

    for f in files:
        visit(f)
    return order


def _resolve_widths(
    rx_items: list[ModuleInfo],
    params: dict[str, Parameter],
) -> dict[str, int]:
    """Resolve parameterized width expressions using known parameter values.

    Supports forms like `ADDR_WIDTH-1:0`, `DATA_WIDTH/2-1:0`, `N-1:0`.
    Returns dict {port_name: resolved_width}.
    """
    out: dict[str, int] = {}
    param_value_str: dict[str, str] = {n: str(p.value) for n, p in params.items()}
    for item in rx_items:
        if item.param or not item.width_expr:
            continue
        expr = item.width_expr
        # Substitute parameter names with their integer values
        substituted = expr
        for pname, pval in sorted(param_value_str.items(), key=lambda kv: -len(kv[0])):
            substituted = re.sub(rf"\b{re.escape(pname)}\b", pval, substituted)
        # Try MSB:LSB form
        msb_lsb = re.match(r"\s*([\w\+\-\*\/\(\)\d\s]+?)\s*:\s*([\w\+\-\*\/\(\)\d\s]+?)\s*$", substituted)
        if msb_lsb:
            try:
                msb = int(eval(msb_lsb.group(1), {"__builtins__": {}}, {}))   # noqa: S307
                lsb = int(eval(msb_lsb.group(2), {"__builtins__": {}}, {}))   # noqa: S307
                out[item.name] = max(msb - lsb + 1, 1)
                continue
            except Exception:                                                  # noqa: BLE001
                pass
        # Try plain integer
        try:
            out[item.name] = max(int(eval(substituted, {"__builtins__": {}}, {})), 1)  # noqa: S307
        except Exception:                                                      # noqa: BLE001
            out.setdefault(item.name, 1)
    return out


def _merge_ports(
    file_path: str,
    pyv_ports: list[dict],
    rx_items: list[ModuleInfo],
    pyv_params: list[dict] | None = None,
) -> tuple[list[Port], list[Parameter]]:
    """Merge PyVerilog dict with regex ModuleInfo list. PyVerilog wins on type fields."""
    ports: dict[str, Port] = {}
    params: dict[str, Parameter] = {}

    # 1) regex base (always succeeds)
    for item in rx_items:
        if item.param:
            params[item.name] = Parameter(
                name=item.name,
                value=int(item.param_value) if item.param_value else 0,
                width=max(item.width, 32),
                signed=(item.sign == "signed"),
            )
        else:
            ports.setdefault(item.name, Port(
                name=item.name,
                direction=item.direction,
                width=max(item.width, 1),
                sign=item.sign,
            ))

    # Resolve parameterized widths now that params are known
    resolved_widths = _resolve_widths(rx_items, params)
    for name, w in resolved_widths.items():
        if name in ports and ports[name].width < w:
            ports[name].width = w

    # 2) PyVerilog augmentation
    for p in pyv_ports:
        name = p.get("name", "")
        if not name:
            continue
        existing = ports.get(name)
        if existing is not None:
            existing.direction = p.get("direction", existing.direction) or existing.direction
            existing.width = max(int(p.get("width", 1) or 1), existing.width)
            sign_val = p.get("sign")
            if sign_val in ("signed", "unsigned"):
                existing.sign = sign_val
        else:
            ports[name] = Port(
                name=name,
                direction=p.get("direction", "input"),
                width=int(p.get("width", 1) or 1),
                sign=p.get("sign", "unsigned"),
            )

    # 3) PyVerilog parameter augmentation. Regex often misses parametrised
    # module headers (parens in default values break its header regex), so
    # PyVerilog's parameters are the authoritative source for DUT parameters.
    for pp in (pyv_params or []):
        pname = pp.get("name", "")
        if not pname:
            continue
        existing_p = params.get(pname)
        if existing_p is None:
            params[pname] = Parameter(
                name=pname,
                value=int(pp.get("value", 0) or 0),
                width=max(int(pp.get("width", 32) or 32), 1),
                signed=bool(pp.get("signed", False)),
            )
        else:
            existing_p.value = int(pp.get("value", existing_p.value) or existing_p.value)

    # Reorder ports by their declaration order in the regex parse (priority),
    # then any PyVerilog-only ports appended at the end.
    declared_order: list[str] = []
    seen: set[str] = set()
    for item in rx_items:
        if not item.param and item.name not in seen:
            declared_order.append(item.name)
            seen.add(item.name)
    for name in ports:
        if name not in seen:
            declared_order.append(name)
            seen.add(name)

    ordered_ports = [ports[n] for n in declared_order if n in ports]
    ordered_params = [Parameter(**p) for p in (params[n].__dict__ for n in params)]
    return ordered_ports, ordered_params


def _infer_clock_reset(ports: list[Port]) -> tuple[list[Clock], list[Reset]]:
    clocks, resets = [], []
    for p in ports:
        ln = p.name.lower()
        if p.width == 1 and ln in {"clk", "hclk", "pclk", "aclk", "mclk", "sclk", "clock"}:
            clocks.append(Clock(name=p.name, width=1, period_ns=10))
        if p.width == 1 and (ln.startswith("rst") or ln.startswith("reset") or ln.startswith("arst") or ln.startswith("nrst")):
            active = 0 if ln.endswith("_n") or ln.endswith("n") else 1
            resets.append(Reset(name=p.name, width=1, active_level=active, duration_cycles=5))
    return clocks, resets


def resolve(rtl_dir: str | Path, top: str) -> Design:
    """Parse an --rtl directory and produce a Design rooted at `top`."""
    files = discover_rtl_files(rtl_dir)
    if not files:
        raise RuntimeError(f"No RTL files found under {rtl_dir}")

    include_dirs: list[str] = [str(Path(rtl_dir).resolve())]
    order = _topological_sort(files)

    all_ports: list[Port] = []
    all_params: list[Parameter] = []

    for f in order:
        src = Path(f).read_text(encoding="utf-8", errors="ignore")
        text = preprocess(src, include_dirs)
        # Regex — always try.
        rx: ParsedFile = rx_parse(text, f)
        rx_items = to_module_info_with_values(rx)
        # PyVerilog — try, ignore on failure. Feed RAW src — pyverilog ships
        # its own preprocessor (`include/`ifdef/`define) and _extract_headers
        # strips comments itself, so our preprocess() layer is redundant here.
        pyv_ports: list[dict] = []
        pyv_params: list[dict] = []
        try:
            py_info = pyv_parse(src, f)
            pyv_ports = py_info.ports
            pyv_params = py_info.parameters
        except PyvParseError as exc:
            log.debug("PyVerilog fallback to regex on %s: %s", f, exc)
        except Exception as exc:                              # noqa: BLE001
            log.debug("PyVerilog unexpected error on %s: %s", f, exc)

        ports, params = _merge_ports(f, pyv_ports, rx_items, pyv_params)
        all_ports.extend(ports)
        all_params.extend(params)

    # Dedup ports by name (keep first occurrence — header-declared port wins).
    dedup_ports: dict[str, Port] = {}
    for p in all_ports:
        dedup_ports.setdefault(p.name, p)

    clocks, resets = _infer_clock_reset(list(dedup_ports.values()))

    return Design(
        top=top,
        rtl_files=order,
        compile_order=order,
        include_dirs=include_dirs,
        clock=clocks,
        reset=resets,
        parameters=all_params,
        ports=list(dedup_ports.values()),
        inferred_protocols=[],
        primary_protocol="",
    )
