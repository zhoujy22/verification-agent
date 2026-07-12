"""PyVerilog wrapper. Used for type/signedness augmentation when it succeeds.

On any PyVerilog internal exception, `parse` raises a `ParseError`. The caller
(port_resolver) falls back to regex-only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when PyVerilog (or any frontend) cannot handle the source."""


@dataclass
class PyVerilogInfo:
    """Subset of ports/parameters we pull out of PyVerilog's AST."""
    module_name: str = ""
    ports: list[dict] = field(default_factory=list)        # [{name, direction, width, sign}]
    parameters: list[dict] = field(default_factory=list)   # [{name, value, width, signed}]


def parse(text: str, filename: str = "<unknown>") -> PyVerilogInfo:
    """Run PyVerilog on preprocessed source. Raises ParseError on failure."""
    try:
        from pyverilog.vparser.parser import parse_verilog  # type: ignore
    except ImportError as exc:
        raise ParseError(f"pyverilog not installed: {exc}") from exc

    try:
        ast, _ = parse_verilog(text)
    except Exception as exc:                                  # noqa: BLE001 — PyVerilog raises bare
        raise ParseError(f"pyverilog failed on {filename}: {exc}") from exc

    info = PyVerilogInfo()
    # Pick the first top-level module declaration.
    for desc in ast.description.definitions:
        if not hasattr(desc, "name") or desc.name != desc.name:
            pass
        if getattr(desc, "__class__.__name__", "") == "ModuleDef":
            info.module_name = desc.name
            info.ports = _extract_ports(desc)
            info.parameters = _extract_params(desc)
            break
    return info


def _extract_ports(module_def) -> list[dict]:
    """Pull ports with direction/width/sign out of PyVerilog's ModuleDef."""
    ports: list[dict] = []
    for p in getattr(module_def, "portlist", []).ports:
        name = p.name
        direction = {
            "input": "input",
            "output": "output",
            "inout": "inout",
        }.get(getattr(p, "direction", "input"), "input")
        width = _eval_width(getattr(p, "width", None))
        sign = "signed" if getattr(p, "signed", False) else "unsigned"
        ports.append({"name": name, "direction": direction, "width": width, "sign": sign})
    return ports


def _extract_params(module_def) -> list[dict]:
    params: list[dict] = []
    for pd in getattr(module_def, "paramlist", []) or []:
        for p in pd.params:
            width = _eval_width(getattr(p, "width", None))
            value = _eval_int(getattr(p, "value", None))
            params.append({
                "name": p.name,
                "value": value,
                "width": width,
                "signed": bool(getattr(p, "signed", False)),
            })
    return params


def _eval_width(node) -> int:
    """Rough integer width: 1 for scalars, else high - low + 1."""
    if node is None:
        return 1
    try:
        return int(getattr(node, "width", 1) or 1)
    except Exception:                                          # noqa: BLE001
        return 1


def _eval_int(node) -> int:
    """Best-effort int evaluation of a PyVerilog expression."""
    if node is None:
        return 0
    try:
        return int(getattr(node, "value", 0) or 0)
    except Exception:                                          # noqa: BLE001
        return 0
