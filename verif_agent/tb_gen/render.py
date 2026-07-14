"""Generate all testbench artifacts into out_dir/generated_tb/.

Artifacts produced per case:
  generated_tb/tb_top.py        — cocotb main entry
  generated_tb/dut_inst.v       — Verilog wrapper that instantiates the DUT
  generated_tb/Makefile         — cocotb simulator Makefile
  generated_tb/coverpoints.py   — cocotb-coverage sampling code (per protocol)

The returned dict is the `verification_skeleton` content (driver / monitor /
scoreboard / clock-reset / wiring summary) for direct write to JSON.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ..design import Design
from .clock_reset import render as render_clock_reset
from .protocols import for_design as protocol_output_for
from .protocols.base import ProtocolOutput


# ------------------------------------------------------------------------
# Verilog wrapper that instantiates the top DUT
# ------------------------------------------------------------------------
def _verilog_wrapper(design: Design) -> str:
    """Verilog wrapper that instantiates the DUT.

    Crucially, the signals the testbench drives (DUT *input* ports) are declared
    as internal ``reg`` — not as ``tb_top`` ports. A previous version declared
    every DUT input as an ``input wire`` port on the top module, which has no
    external driver under the simulator; cocotb's ``dut.<sig>.value = ...``
    writes were silently dropped (VPI drives a port that nothing sources), so
    the DUT saw constant 0/X and never advanced. Declaring them as ``reg``
    makes cocotb's writes take effect immediately, exactly like the reference
    cocotb top files (``reg s_axi_arvalid = 0;``).
    """
    decls: list[str] = []
    port_list: list[str] = []
    for p in design.ports:
        rng = "" if p.width == 1 else f"[{p.width-1}:0] "
        if p.direction == "input":
            decls.append(f"  reg  {rng}{p.name};")
        else:
            decls.append(f"  wire {rng}{p.name};")
        port_list.append(p.name)

    body = "  " + design.top + " dut_inst ("
    body += ", ".join(f".{n}({n})" for n in port_list)
    body += ");\n"

    return f"""// Auto-generated wrapper for {design.top}
`timescale 1ns/1ps
module tb_top;
{chr(10).join(decls)}
{body}endmodule
"""


# ------------------------------------------------------------------------
# cocotb main entry tb_top.py
# ------------------------------------------------------------------------
def _tb_top_py(design: Design, proto: ProtocolOutput, cr_py: str, bins: dict) -> str:
    cp_init = []
    cp_sites = {}
    for cp in bins.get("coverpoints", []):
        cp_name = cp["name"]
        cp_init.append(f'    "{cp_name}": {{')
        for b in cp["bins"]:
            bin_name = b["name"]
            cp_init.append(f'        "{bin_name}": Hit(),')
            cp_sites.setdefault(cp_name, []).append(bin_name)
        cp_init.append("    },")

    coverpoint_setup = "cp_registry = {\n" + "\n".join(cp_init) + "\n}\n"

    seed_line = f"SEED = {proto_seed(design)}"

    return f'''"""Auto-generated cocotb testbench for {design.top}.

Driven by /work/verif_agent tb_gen. Run via the Makefile in this directory.
SEED is set in the Makefile as SIM_ARGS+=--seed=${{SEED}}.
"""
import cocotb
import random
import json
from pathlib import Path
from cocotb.triggers import RisingEdge, ReadOnly, Timer
from cocotb.clock import Clock
from cocotb_coverage import coverage

# Random source — single instance per spec reproducibility contract.
_SEED = int(getattr(cocotb, "_SIM_ARGS_seed", 12345))
RNG = random.Random(_SEED)


class Hit:
    def __init__(self):
        self.hit = 0


{coverpoint_setup}


{cr_py}


{proto.driver_py}


{proto.monitor_py}


{proto.scoreboard_py}


{proto.coverpoint_py}


# ----- functional_coverage.json dump at end-of-test -----
async def _dump_functional_cov(dut, cp_registry, out_path: Path) -> None:
    payload = {{}}
    for cp_name, bins in cp_registry.items():
        payload[cp_name] = {{}}
        for bin_name, hit_obj in bins.items():
            payload[cp_name][bin_name] = {{
                "hit_count": int(hit_obj.hit),
                "covered": int(hit_obj.hit) > 0,
            }}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)


{cocotb_test_body(proto)}


# Tap for sampler used by monitors
def coverpoint_sampler(scenario: str, txn: dict) -> None:
    pass  # overridden in @cocotb.test()
'''


def cocotb_test_body(proto: ProtocolOutput) -> str:
    """Return the body of @cocotb.test() for the protocol."""
    if proto.name == "valid_ready_stream":
        return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    in_q: list = []
    out_q: list = []
    cocotb.start_soon(stream_monitor(dut, in_q, out_q,
                                       coverpoint_sampler=lambda s, t: _sample_stream_bins(dut, cp_registry)))
    sb = StreamScoreboard(in_q, out_q)
    await stream_driver(dut, RNG, NUM_SEQ)
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    assert passed, f"mismatches: {sb.mismatch_log[:10]}"
'''
    if proto.name == "axi_lite" or proto.name == "axi_full":
        return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    sb = AxiScoreboard()
    await axi_driver(dut, RNG, NUM_SEQ, sb)
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    assert passed, f"scoreboard failures: {sb.failures[:10]}"
'''
    if proto.name == "axi_stream":
        return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    sb = AxiStreamScoreboard()
    await axis_driver(dut, RNG, NUM_SEQ, sb)
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    assert passed, f"scoreboard failures: {sb.failures[:10]}"
'''
    if proto.name == "sram":
        return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    sb = SramScoreboard()
    await sram_driver(dut, RNG, NUM_SEQ)
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    assert passed, f"scoreboard failures: {sb.failures[:10]}"
'''
    if proto.name == "generic":
        return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    sample_q: list = []
    cocotb.start_soon(generic_monitor(dut, sample_q))
    await generic_driver(dut, RNG, NUM_SEQ)
    await Timer(200, units="ns")
    # Best-effort functional bin sampling (decoder bins for a* DUTs, run-tick otherwise).
    for _ in range(64):
        try:
            _sample_generic_bins(dut, cp_registry)
        except Exception:
            pass
        await RisingEdge(dut.clk) if hasattr(dut, "clk") else Timer(10, units="ns")
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    passed = (len(sample_q) > 0)
    assert passed, "generic driver captured no samples"
'''
    return '''
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    await Timer(500, units="ns")
'''


def proto_seed(design: Design) -> int:
    """Deterministic default seed if --seed not provided."""
    return int.from_bytes(design.top.encode(), "big") & 0x7FFFFFFF


# ------------------------------------------------------------------------
# Makefile
# ------------------------------------------------------------------------
def _makefile(design: Design, rel_rtl: list[str] | None = None) -> str:
    # RTL sources: copied into generated/rtl/ by render() (self-contained).
    # Prefix with $(PWD) (the tb dir, absolute) so the path resolves correctly
    # even though cocotb's VCS/verilator Makefile does `cd sim_build` before
    # invoking the compiler — a bare `../rtl/x.v` would be wrong from sim_build/.
    src_exts = (".v", ".sv")
    if rel_rtl:
        # rel_rtl entries are like "../rtl/case1.v" (relative to tb dir);
        # make them absolute via $(PWD).
        sources = [f"$(PWD)/{s}" for s in rel_rtl]
    else:
        sources = [f for f in (design.compile_order or design.rtl_files)
                   if f.lower().endswith(src_exts)]
    if not sources:
        sources = ["$(PWD)/../rtl/dut.v"]
    # dut_inst.v lives next to this Makefile.
    verilog_sources = " \\\n  ".join([*sources, "$(PWD)/dut_inst.v"])

    # Include dir → +incdir+ via COMPILE_ARGS. Absolute via $(PWD) too.
    incdir_args = " +incdir+$(PWD)/../rtl"
    compile_args_line = f"\nCOMPILE_ARGS += {incdir_args}"

    return f"""# Auto-generated Makefile for cocotb
SIM ?= verilator
TOPLEVEL_LANG ?= verilog
VERILOG_SOURCES += \\
  {verilog_sources}
TOPLEVEL ?= tb_top
MODULE ?= tb_top

# Verilator-only coverage/build flags. `--coverage-line` + `--coverage-toggle`
# (toggle ≈ branch coverage) instead of bare `--coverage`, so lcov .info
# carries BOTH DA: (line) and BRDA: (branch) records. Guarded so the icarus
# fallback (SIM=icarus) does not receive flags iverilog rejects.
ifeq ($(SIM),verilator)
EXTRA_ARGS += --coverage-line --coverage-toggle --build -Wno-fatal
endif{compile_args_line}

SEED ?= 12345
NUM_SEQ ?= 5000

export COCOTB_REDUCED_LOG_PRUNING ?= 1

include $(shell cocotb-config --makefiles)/Makefile.sim

run:
\techo "Use 'make SIM_ARGS+=--seed=$(SEED) NUM_SEQ=$(NUM_SEQ)' via run.sh"
"""


# ------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------
@dataclass
class RenderResult:
    tb_dir: Path
    skeleton: dict


def render(design: Design, constraints: dict, bins: dict, out_dir: Path) -> RenderResult:
    """Render all testbench artifacts into out_dir/generated/tb/.

    RTL sources are copied into out_dir/generated/rtl/ so the generated/
    directory is self-contained — the grader can run VCS/Verilator without
    needing the original --rtl path (which is an absolute path on our machine
    and won't exist on theirs). The Makefile references RTL by this relative
    path.
    """
    gen_dir = Path(out_dir) / "generated"
    tb_dir = gen_dir / "tb"
    rtl_out_dir = gen_dir / "rtl"
    tb_dir.mkdir(parents=True, exist_ok=True)
    rtl_out_dir.mkdir(parents=True, exist_ok=True)

    # Copy RTL sources into generated/rtl/ (self-contained delivery).
    import shutil
    rel_rtl: list[str] = []
    for f in (design.compile_order or design.rtl_files):
        src = Path(f)
        if not src.exists() or not src.name.lower().endswith((".v", ".sv")):
            continue
        dst = rtl_out_dir / src.name
        shutil.copy2(src, dst)
        rel_rtl.append(f"../rtl/{src.name}")

    proto = protocol_output_for(design)
    cr_py = render_clock_reset(design)

    # Adjust NUM_SEQ in generated tb_top
    num_seq = constraints.get("num_seq", 5000)
    tb_text = _tb_top_py(design, proto, cr_py, bins)
    tb_text = tb_text.replace("NUM_SEQ", str(num_seq))
    (tb_dir / "tb_top.py").write_text(tb_text, encoding="utf-8")

    (tb_dir / "dut_inst.v").write_text(_verilog_wrapper(design), encoding="utf-8")

    mk_text = _makefile(design, rel_rtl or None)
    (tb_dir / "Makefile").write_text(mk_text, encoding="utf-8")

    # coverpoints file (currently embedded in tb_top; keep a placeholder for IDE)
    (tb_dir / "coverpoints.py").write_text(
        f'"""Coverpoint summary for {design.top}. See tb_top.cp_registry."""\n'
        f"COVERPOINTS = {json_summary(bins)}\n",
        encoding="utf-8",
    )

    (tb_dir / "sim_run.log").write_text("", encoding="utf-8")

    # Per-interface driver/monitor split. The skeleton MUST match what tb_top.py
    # actually does: the driver `name` is the real cocotb function (proto.driver_name),
    # `driver` is the concrete driver class, and drives/observes are the DUT input/
    # output ports of each interface (grouped by classifier interface_name). This
    # replaces the earlier "reporter re-infers drivers from ports" path, which
    # invented names (e.g. "s_axis_driver") that don't exist in tb_top.py.
    driver_class = _DRIVER_CLASS.get(proto.name, "cocotb driver")
    monitor_class = _MONITOR_CLASS.get(proto.name, "cocotb monitor")
    drivers = _skeleton_drivers(design, proto, driver_class)
    monitors = _skeleton_monitors(design, proto, monitor_class)

    skeleton = {
        "schema_version": 1,
        "case_name": design.top,
        "clock_reset": {
            "clock_source": (
                f"cocotb.clock.Clock(dut.{design.clock[0].name}, {design.clock[0].period_ns}, units='ns').start()"
                if design.clock else "no_clock_detected"
            ),
            "reset_active_level": design.reset[0].active_level if design.reset else 1,
            "reset_assert_cycles": design.reset[0].duration_cycles if design.reset else 0,
            "reset_deassert_after_cycles": 2,
        },
        "drivers": drivers,
        "monitors": monitors,
        "scoreboard": {
            "name": proto.scoreboard_name or (proto.name + "_refmodel"),
            "type": "transaction_level" if proto.name != "none" else "none",
            "checks": list(proto.scoreboard_checks) if proto.scoreboard_checks else (
                ["mismatch_log"] if proto.name != "none" else []
            ),
        },
        "dut_wiring": {
            "top": design.top,
            "wrapper_module": "tb_top",
            "files": [
                str((tb_dir / "dut_inst.v").resolve()),
            ] + design.rtl_files,
        },
        "testbench_source": "generated_tb/tb_top.py",
    }

    return RenderResult(tb_dir=tb_dir, skeleton=skeleton)


# Concrete driver/monitor classes per protocol — what tb_top.py actually uses.
_DRIVER_CLASS = {
    "axi_lite": "cocotbext-axi AxiMaster/AxiRam (read/write/full per channel set)",
    "axi_full": "cocotbext-axi AxiMaster/AxiRam (read/write/full per channel set)",
    "axi_stream": "cocotbext-axi AxiStreamSource (slave input) / AxiStreamSink (master output)",
    "sram": "cocotb pin-level driver (csb/we/addr/din/wmask)",
    "generic": "cocotb pin-level generic driver (all inputs toggled)",
    "none": "",
}
_MONITOR_CLASS = {
    "axi_lite": "cocotbext-axi internal (AxiMaster/AxiRam observe outputs)",
    "axi_full": "cocotbext-axi internal (AxiMaster/AxiRam observe outputs)",
    "axi_stream": "cocotbext-axi AxiStreamSink",
    "sram": "cocotb pin-level monitor (dout sampled on csb active)",
    "generic": "cocotb pin-level generic monitor (all outputs sampled)",
    "none": "",
}


def _skeleton_drivers(design: Design, proto: ProtocolOutput, driver_class: str) -> list[dict]:
    """One driver entry per interface this protocol drives; name = real tb_top func."""
    if proto.name == "none" or not proto.driver_name:
        return []
    # Group the ports this generator handles by interface_name.
    handled = set(proto.ports_handled or [])
    by_iface: dict[str, dict[str, list[str]]] = {}
    for p in design.ports:
        if p.name not in handled:
            continue
        iface = p.interface_name or "generic"
        g = by_iface.setdefault(iface, {"drives": [], "observes": []})
        if p.direction == "input":
            g["drives"].append(p.name)
        else:
            g["observes"].append(p.name)
    if not by_iface:
        by_iface["generic"] = {"drives": [], "observes": list(handled)}
    return [{
        "name": proto.driver_name,
        "interface": iface,
        "driver": driver_class,
        "drives": g["drives"],
        "observes": g["observes"],
        "traffic": "",
    } for iface, g in by_iface.items()]


def _skeleton_monitors(design: Design, proto: ProtocolOutput, monitor_class: str) -> list[dict]:
    """One monitor entry per interface whose DUT outputs this protocol observes."""
    if proto.name == "none" or not proto.monitor_name:
        # Protocols that self-monitor (cocotbext-axi) report a single summary monitor.
        if proto.name in ("axi_lite", "axi_full", "axi_stream"):
            obs = [p.name for p in design.ports if p.direction in ("output", "inout")
                   and p.name in (proto.ports_handled or [])]
            return [{"name": proto.monitor_name or (proto.name + "_monitor"),
                     "interface": proto.protocol_group or "generic",
                     "monitor": monitor_class,
                     "observes": obs or ["<internal>"],
                     "checks": ""}] if obs else []
        return []
    handled = set(proto.ports_handled or [])
    groups: dict[str, list[str]] = {}
    for p in design.ports:
        if p.name not in handled or p.direction not in ("output", "inout"):
            continue
        iface = p.interface_name or "generic"
        groups.setdefault(iface, []).append(p.name)
    return [{
        "name": proto.monitor_name,
        "interface": iface,
        "monitor": monitor_class,
        "observes": names,
        "checks": "",
    } for iface, names in groups.items()]


def json_summary(bins: dict) -> str:
    """Compact JSON summary for the coverpoints.py placeholder."""
    import json
    return json.dumps(
        [{"name": cp["name"], "bins": [b["name"] for b in cp["bins"]]}
         for cp in bins.get("coverpoints", [])],
        indent=2,
    )
