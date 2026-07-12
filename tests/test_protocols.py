"""Unit tests for the testbench generator stack.

Doesn't drive a simulator — just verifies the Python source strings are valid
and reference the right protocol-specific symbols.
"""
from __future__ import annotations

from pathlib import Path

from verif_agent.classifier import classify
from verif_agent.coverage_definer import define
from verif_agent.constraints_gen import generate
from verif_agent.design import Design, Port
from verif_agent.tb_gen.render import render
from verif_agent.tb_gen.protocols import for_design as protocol_for


def _make_stream_design() -> Design:
    return Design(top="stream_dut", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="in_valid", direction="input", width=1),
        Port(name="in_ready", direction="output", width=1),
        Port(name="in_data", direction="input", width=8),
        Port(name="out_valid", direction="output", width=1),
        Port(name="out_ready", direction="input", width=1),
        Port(name="out_data", direction="output", width=8),
    ])


def _make_sram_design() -> Design:
    return Design(top="sram_dut", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="csb", direction="input", width=1),
        Port(name="we", direction="input", width=1),
        Port(name="addr", direction="input", width=16),
        Port(name="din", direction="input", width=8),
        Port(name="dout", direction="output", width=8),
    ])


def _make_axi_lite_design() -> Design:
    ports = [
        Port(name="aclk", direction="input", width=1),
        Port(name="aresetn", direction="input", width=1),
        Port(name="awaddr", direction="input", width=32),
        Port(name="awvalid", direction="input", width=1),
        Port(name="awready", direction="output", width=1),
        Port(name="wdata", direction="input", width=32),
        Port(name="wstrb", direction="input", width=4),
        Port(name="wvalid", direction="input", width=1),
        Port(name="wready", direction="output", width=1),
        Port(name="bresp", direction="output", width=2),
        Port(name="bvalid", direction="output", width=1),
        Port(name="bready", direction="input", width=1),
        Port(name="araddr", direction="input", width=32),
        Port(name="arvalid", direction="input", width=1),
        Port(name="arready", direction="output", width=1),
        Port(name="rdata", direction="output", width=32),
        Port(name="rresp", direction="output", width=2),
        Port(name="rvalid", direction="output", width=1),
        Port(name="rready", direction="input", width=1),
    ]
    return Design(top="axi_dut", ports=ports)


def test_stream_protocol_generator_has_all_pieces():
    d = _make_stream_design()
    classify(d)
    proto = protocol_for(d)
    assert proto.name == "valid_ready_stream"
    assert "async def stream_driver" in proto.driver_py
    assert "stream_monitor" in proto.monitor_py
    assert "StreamScoreboard" in proto.scoreboard_py
    assert "_sample_stream_bins" in proto.coverpoint_py
    # Note: we do NOT assert specific port literal strings inside the source —
    # they get f-string interpolated. The render-time test below covers that.


def test_sram_protocol_generator():
    d = _make_sram_design()
    classify(d)
    proto = protocol_for(d)
    assert proto.name == "sram"
    assert "SramScoreboard" in proto.scoreboard_py
    assert "sram_driver" in proto.driver_py
    assert "addr" in proto.driver_py


def test_axi_lite_protocol_generator():
    d = _make_axi_lite_design()
    classify(d)
    proto = protocol_for(d)
    assert proto.name == "axi_lite"
    assert "axi_lite_driver" in proto.driver_py
    assert "AxiLiteScoreboard" in proto.scoreboard_py
    assert proto.handshake == "valid_ready"


def test_render_writes_all_files(tmp_path: Path):
    d = _make_stream_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=42)
    res = render(d, constraints, bins, tmp_path)
    assert (res.tb_dir / "tb_top.py").exists()
    assert (res.tb_dir / "dut_inst.v").exists()
    assert (res.tb_dir / "Makefile").exists()
    assert (res.tb_dir / "coverpoints.py").exists()
    assert (res.tb_dir / "sim_run.log").exists()


def test_render_skeleton_has_required_keys(tmp_path: Path):
    d = _make_stream_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=42)
    res = render(d, constraints, bins, tmp_path)
    skeleton = res.skeleton
    for k in ("clock_reset", "drivers", "monitors", "scoreboard", "dut_wiring", "testbench_source"):
        assert k in skeleton
    assert skeleton["drivers"], "drivers list must be non-empty per spec"
    assert skeleton["monitors"], "monitors list must be non-empty per spec"


def test_render_tb_top_imports_and_uses_protocol_specific_coro(tmp_path: Path):
    d = _make_sram_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=1, num_seq=5000)
    res = render(d, constraints, bins, tmp_path)
    text = (res.tb_dir / "tb_top.py").read_text(encoding="utf-8")
    assert "import cocotb" in text
    assert "sram_driver(" in text
    assert "SramScoreboard(" in text
    assert "setup_clock_reset" in text
    assert "RNG = random.Random" in text
    # NUM_SEQ gets replaced with the integer literal at render time.
    assert "5000" in text
