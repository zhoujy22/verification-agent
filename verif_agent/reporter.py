"""Write all required JSON outputs to submission_out/<case>/.

Spec-required (each written in the official public-example format, under a
single top-level wrapper key):
  - design.json                 -> {"design": {...}}
  - verification_skeleton.json  -> {"verification_skeleton": {...}}
  - constraints.json            -> {"constraint_random_test": {...}}
  - coverage_bins.json          -> {"coverage_bins": {...}}
  - functional_coverage.json    -> {"functional_coverage": {...}}
  - coverage_result.json        -> {"coverage_result": {...}}

Internal-only (not required by spec, kept for self-check):
  - report.json
"""
from __future__ import annotations

import json
import logging
import platform
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


REQUIRED_FILES = [
    "design.json",
    "verification_skeleton.json",
    "constraints.json",
    "coverage_bins.json",
    "functional_coverage.json",
    "coverage_result.json",
    "report.json",
]


@dataclass
class ReporterInputs:
    design: dict
    skeleton: dict
    constraints: dict
    bins: dict
    functional: dict
    cov_result: dict
    stages: dict
    reproducible_command: str
    tool: str
    failures: list[str]
    out_dir: Path
    case_name: str = ""


def write_all(inputs: ReporterInputs) -> None:
    out_dir = Path(inputs.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "design.json").write_text(_pretty(_to_design(inputs)), encoding="utf-8")
    (out_dir / "verification_skeleton.json").write_text(_pretty(_to_skeleton(inputs)), encoding="utf-8")
    (out_dir / "constraints.json").write_text(_pretty(_to_constraints(inputs)), encoding="utf-8")
    (out_dir / "coverage_bins.json").write_text(_pretty(_to_coverage_bins(inputs)), encoding="utf-8")
    (out_dir / "functional_coverage.json").write_text(_pretty(_to_functional(inputs)), encoding="utf-8")
    (out_dir / "coverage_result.json").write_text(_pretty(_to_coverage_result(inputs)), encoding="utf-8")

    out_paths = {f: str((out_dir / f).resolve()) for f in REQUIRED_FILES[:6]}
    out_paths["generated_tb_dir"] = str((out_dir / "generated_tb").resolve())
    out_paths["generated_tests_dir"] = str((out_dir / "generated_tests").resolve())

    report = {
        "schema_version": 1,
        "case_name": inputs.case_name or inputs.design.get("top", ""),
        "stages": inputs.stages,
        "outputs": out_paths,
        "coverage_summary": {
            "line": inputs.cov_result.get("line"),
            "branch": inputs.cov_result.get("branch"),
            "functional": inputs.cov_result.get("functional"),
            "combined_C": inputs.cov_result.get("combined_C"),
        },
        "reproducible_command": inputs.reproducible_command,
        "environment": {
            "python": platform.python_version(),
            "tool": inputs.tool,
            "docker_image": "verif-agent:0.1.0",
            "platform": platform.platform(),
        },
        "failures": inputs.failures[:50],
    }
    (out_dir / "report.json").write_text(_pretty(report), encoding="utf-8")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _case(inputs: ReporterInputs) -> str:
    return inputs.case_name or inputs.design.get("top", "") or "design"


def _status(inputs: ReporterInputs) -> str:
    st = inputs.stages or {}
    return "passed" if (st.get("simulate") == "ok" and st.get("coverage_collect") == "ok") else "failed"


_SIM_NAMES = {"verilator": "Verilator", "icarus": "Icarus Verilog", "vcs": "VCS"}


def _sim_name(inputs: ReporterInputs) -> str:
    return _SIM_NAMES.get((inputs.tool or "").lower(), inputs.tool or "none")


# AXI channel mapping: classifier protocol_group -> interface channel letter
_AXI_CHAN = {"axi_aw": "AW", "axi_w": "W", "axi_b": "B",
             "axi_ar": "AR", "axi_r": "R", "axi_a": "A"}


def _iface_of_port(port: dict) -> tuple[str | None, str | None]:
    """Map a port to (interface_name, channel) using the classifier-assigned
    interface_name (covers AXI / AXI-Stream / valid-ready stream / SRAM / APB / custom).

    Falls back to protocol_group-based logic for legacy data without interface_name.
    """
    # Prefer classifier-assigned interface_name (set in pass 4 + LLM naming)
    iface = port.get("interface_name") or ""
    if iface:
        pg = (port.get("protocol_group") or "").lower()
        # Derive channel for AXI groups
        if pg.startswith("axi_"):
            return iface, _AXI_CHAN.get(pg)
        if pg.startswith("axis_"):
            return iface, "T"
        if pg.startswith("stream_"):
            return iface, "T"
        # SRAM, APB, custom: no channel
        return iface, None

    # Fallback: derive from protocol_group + port name (legacy path)
    pg = (port.get("protocol_group") or "").lower()
    name = (port.get("name") or "").lower()
    if pg in ("", "clk", "rst", "passive"):
        return None, None
    if pg.startswith("axi_"):
        iface_fb = "m_axi" if name.startswith("m_axi_") else ("s_axi" if name.startswith("s_axi_") else "axi")
        return iface_fb, _AXI_CHAN.get(pg)
    if pg.startswith("axis_"):
        return ("m_axis" if name.startswith("m_axis_") else "s_axis"), "T"
    if pg.startswith("stream_"):
        m = re.match(r"^(.+?)_(valid|ready|data)$", name)
        return (m.group(1) if m else "stream"), "T"
    if pg == "sram":
        return "sram", None
    if pg == "apb":
        return "apb", None
    if pg.startswith("custom:"):
        # Use the prefix as interface name (before LLM naming is applied)
        return pg.split(":", 1)[1], None
    return None, None


def _iface_role(iface: str) -> str:
    if iface.startswith("s_") or iface in ("input", "sram", "apb"):
        return "slave"
    if iface.startswith("m_") or iface == "output":
        return "master"
    # custom/semantic names (e.g. "configuration", "status") default to slave
    return "slave"


def _build_interfaces(ports: list[dict]) -> dict:
    """Group ports into interfaces using protocol_group; channels come from AXI groups."""
    chans: dict[str, set[str]] = {}
    for p in ports:
        iface, ch = _iface_of_port(p)
        if not iface:
            continue
        chans.setdefault(iface, set())
        if ch:
            chans[iface].add(ch)
    return {iface: {"role": _iface_role(iface), "channels": sorted(cs)}
            for iface, cs in chans.items()}


def _infer_drivers(ports: list[dict]) -> list[dict]:
    """One driver per interface (drives the interface's DUT inputs, observes its outputs)."""
    groups: dict[str, dict[str, list[str]]] = {}
    for p in ports:
        iface, _ = _iface_of_port(p)
        if not iface:
            continue
        g = groups.setdefault(iface, {"drives": [], "observes": []})
        if p.get("direction") == "input":
            g["drives"].append(p["name"])
        else:
            g["observes"].append(p["name"])
    return [{"name": f"{iface}_driver", "interface": iface, "driver": "cocotb driver",
             "drives": g["drives"], "observes": g["observes"], "traffic": ""} for iface, g in groups.items()]


def _infer_monitors(ports: list[dict]) -> list[dict]:
    """One monitor per interface, observing only that interface's DUT outputs."""
    groups: dict[str, list[str]] = {}
    for p in ports:
        iface, _ = _iface_of_port(p)
        if not iface or p.get("direction") not in ("output", "inout"):
            continue
        groups.setdefault(iface, []).append(p["name"])
    return [{"name": f"{iface}_monitor", "interface": iface, "monitor": "cocotb monitor",
             "observes": names, "checks": ""} for iface, names in groups.items()]


# ---------------------------------------------------------------------------
# transforms: internal schema -> official public-example schema
# ---------------------------------------------------------------------------

def _to_design(inputs: ReporterInputs) -> dict:
    d = inputs.design
    clocks = d.get("clock") or []
    resets = d.get("reset") or []
    params = d.get("parameters") or []
    desc = d.get("description") or {}
    interfaces = _build_interfaces(d.get("ports") or [])
    for iface, info in interfaces.items():
        llm_role = (desc.get("interfaces") or {}).get(iface, {}).get("role")
        if llm_role:
            info["role"] = llm_role
    primary = d.get("primary_protocol") or ""
    function = desc.get("function") or (f"{primary} sub-system" if primary else "unrecognized RTL")
    clk = clocks[0]["name"] if clocks else ""
    rst = resets[0]["name"] if resets else ""
    rst_active = "high" if (resets and resets[0].get("active_level", 1) == 1) else "low"
    rtl_first = d.get("rtl_files") or []
    return {"design": {
        "name": _case(inputs),
        "rtl": f"rtl/{Path(rtl_first[0]).name}" if rtl_first else "",
        "top_module": d.get("top", ""),
        "generated_cocotb_top": "generated_tb/dut_inst.v",
        "function": function,
        "configuration_under_test": {p.get("name"): p.get("value") for p in params},
        "clock_reset": {"clock": clk, "reset": rst, "reset_active": rst_active},
        "interfaces": interfaces,
        "related_testbench_note": desc.get("related_testbench_note") or {"user_named_path": "", "finding": ""},
        "vcs_compatibility_note": desc.get("vcs_compatibility_note") or {"finding": ""},
    }}


def _to_constraints(inputs: ReporterInputs) -> dict:
    c = inputs.constraints or {}
    txn_vars: dict[str, dict] = {}
    for v in c.get("random_variables") or []:
        name = v.get("name")
        if not name:
            continue
        width = v.get("width", 32)
        entry: dict = {"type": "bitvec", "width": width}
        if "range" in v:
            entry["min"], entry["max"] = v["range"][0], v["range"][1]
        if "weights" in v:
            entry["type"] = "enum"
            entry["values"] = list(v["weights"].keys())
        txn_vars[name] = entry
    hard = [pc.get("expr", "") for pc in (c.get("protocol_constraints") or [])]
    return {"constraint_random_test": {
        "name": f"{_case(inputs)}_constraints",
        "solver_target": "Z3-compatible integer and enum constraints",
        "seed": c.get("seed"),
        "sequence_count": c.get("num_seq"),
        "transaction_variables": txn_vars,
        "hard_constraints": hard,
        "coverage_guidance_constraints": [],
        "objective": {
            "primary": "maximize functional coverage",
            "secondary": "exercise corner cases",
            "reported_metric": ["functional_coverage", "composite_coverage"],
        },
        "vcs_execution_model": {
            "json_supported": False,
            "method": "cocotb testbench consumes constraints.json",
            "implementation": "cocotb reads transaction_variables and drives the DUT",
            "vcs_options": [],
        },
    }}


def _to_coverage_bins(inputs: ReporterInputs) -> dict:
    b = inputs.bins or {}
    bins = [
        {"name": cp.get("name"), "values": [bi.get("name") for bi in cp.get("bins") or []]}
        for cp in b.get("coverpoints") or []
    ]
    return {"coverage_bins": {
        "name": f"{_case(inputs)}_functional_bins",
        "measurement": "Bin hits are sampled by the cocotb testbench and reported in functional_coverage.json.",
        "bins": bins,
    }}


def _to_coverage_result(inputs: ReporterInputs) -> dict:
    cr = inputs.cov_result or {}
    c = inputs.constraints or {}
    return {"coverage_result": {
        "design": _case(inputs),
        "status": _status(inputs),
        "simulator": {"name": _sim_name(inputs), "version": ""},
        "run": {
            "type": "cocotb",
            "test": "generated_tb/tb_top.py",
            "seed": c.get("seed"),
            "sequence_count": c.get("num_seq"),
            "sim_time_ns": 0,
            "wall_time_s": 0,
        },
        "coverage": {
            "line":       {"covered": cr.get("line_hits", 0), "total": cr.get("line_total", 0), "percent": cr.get("line", 0.0)},
            "branch":     {"covered": cr.get("branch_hits", 0), "total": cr.get("branch_total", 0), "percent": cr.get("branch", 0.0)},
            "functional": {"covered": cr.get("functional_hits", 0), "total": cr.get("functional_total", 0), "percent": cr.get("functional", 0.0)},
            "composite":  {"formula": "0.4*line + 0.3*branch + 0.3*functional", "percent": cr.get("combined_C", 0.0)},
        },
        "artifacts": {
            "urg_report": "",
            "results_xml": "",
            "functional_coverage": "functional_coverage.json",
        },
    }}


def _to_functional(inputs: ReporterInputs) -> dict:
    f = inputs.functional or {}
    coverpoints = []
    for cp in f.get("per_coverpoint") or []:
        bins = cp.get("bins") or []
        coverpoints.append({
            "name": cp.get("name"),
            "covered_bins": sum(1 for b in bins if b.get("covered")),
            "total_bins": len(bins),
            "bins": [{"name": b.get("name"), "hits": b.get("hit_count", 0), "covered": bool(b.get("covered"))} for b in bins],
        })
    return {"functional_coverage": {
        "design": _case(inputs),
        "status": _status(inputs),
        "source": "cocotb transaction sampling",
        "covered_bins": f.get("covered_bins", 0),
        "total_bins": f.get("valid_bins", 0),
        "percent": f.get("functional_coverage_pct", 0.0),
        "coverpoints": coverpoints,
    }}


def _to_skeleton(inputs: ReporterInputs) -> dict:
    s = inputs.skeleton or {}
    d = inputs.design or {}
    ports = d.get("ports") or []
    clocks = d.get("clock") or []
    resets = d.get("reset") or []
    clk = clocks[0]["name"] if clocks else "clk"
    rst = resets[0]["name"] if resets else "rst"
    cr = s.get("clock_reset") or {}

    # Prefer inferring drivers/monitors from port DIRECTION — this correctly
    # puts DUT inputs into `drives` and DUT outputs into `observes`, grouped by
    # AXI interface (s_axi / m_axi). The skeleton's own driver list lumps every
    # port (incl. clk/rst and both directions) into handles_ports with no split,
    # so it is only used as a non-AXI fallback.
    input_drivers = _infer_drivers(ports)
    if not input_drivers:
        input_drivers = [{
            "name": dr.get("name"),
            "interface": dr.get("interface"),
            "driver": dr.get("name"),
            "drives": dr.get("handles_ports") or [],
            "observes": [],
            "traffic": "",
        } for dr in (s.get("drivers") or [])]

    output_monitors = _infer_monitors(ports)
    if not output_monitors:
        output_monitors = [{
            "name": m.get("name"),
            "interface": m.get("interface"),
            "monitor": m.get("name"),
            "observes": [sm.get("port") for sm in m.get("samples") or []],
            "checks": "",
        } for m in (s.get("monitors") or [])]

    sb = s.get("scoreboard") or {}
    return {"verification_skeleton": {
        "clock_reset_generation": {
            "clock": {"signal": clk, "period_ns": 10, "implementation": cr.get("clock_source", "")},
            "reset": {
                "signal": rst,
                "active_high_cycles": cr.get("reset_assert_cycles", 0),
                "post_reset_idle_cycles": cr.get("reset_deassert_after_cycles", 0),
                "implementation": "",
            },
        },
        "input_drivers": input_drivers,
        "output_monitors": output_monitors,
        "scoreboard": {
            "model": sb.get("name", ""),
            "checks": sb.get("checks") or [],
        },
    }}


def assert_all_outputs_present(out_dir: Path) -> tuple[bool, list[str]]:
    missing = []
    for f in REQUIRED_FILES:
        if not (Path(out_dir) / f).exists():
            missing.append(f)
    return (not missing), missing


def _pretty(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)
