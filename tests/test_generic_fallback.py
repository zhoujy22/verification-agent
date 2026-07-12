"""Tests for the Plan A generic fallback protocol + APB coverage bins."""
from __future__ import annotations

from pathlib import Path

import pytest

from verif_agent.classifier import classify
from verif_agent.constraints_gen import generate
from verif_agent.coverage_definer import define
from verif_agent.design import Design, Port
from verif_agent.tb_gen.protocols import PROTOCOL_REGISTRY, for_design
from verif_agent.tb_gen.render import render


def _make_apb_design() -> Design:
    return Design(top="apb_dut", ports=[
        Port(name="pclk", direction="input", width=1),
        Port(name="presetn", direction="input", width=1),
        Port(name="psel", direction="input", width=1),
        Port(name="penable", direction="input", width=1),
        Port(name="pwrite", direction="input", width=1),
        Port(name="paddr", direction="input", width=16),
        Port(name="pwdata", direction="input", width=32),
        Port(name="prdata", direction="output", width=32),
        Port(name="pready", direction="output", width=1),
    ])


def _make_unknown_design() -> Design:
    """A truly passive interface — e.g. a GPIO block."""
    return Design(top="gpio_dut", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="io_in0", direction="input", width=8),
        Port(name="io_in1", direction="input", width=1),
        Port(name="io_out0", direction="output", width=8),
        Port(name="io_out1", direction="output", width=1),
    ])


def test_apb_protocol_generator():
    d = _make_apb_design()
    classify(d)
    proto = for_design(d)
    assert proto.name == "apb"
    assert "apb_driver" in proto.driver_py
    assert "ApbScoreboard" in proto.scoreboard_py
    assert "psel" in proto.driver_py
    assert "paddr" in proto.driver_py


def test_apb_coverage_bins():
    d = _make_apb_design()
    classify(d)
    bins = define(d)
    cp_names = {cp["name"] for cp in bins["coverpoints"]}
    assert {"cp_pwrite", "cp_penable", "cp_addr_align", "cp_pready"} <= cp_names


def test_generic_fallback_protocol_chosen_for_passive_design():
    """When no protocol matches, the generic protocol is selected (Plan A)."""
    d = _make_unknown_design()
    classify(d)
    assert d.primary_protocol == ""
    proto = for_design(d)
    assert proto.name == "generic"
    assert proto.handshake == "level"
    # Must have non-empty driver/monitor (the whole point of Plan A).
    assert proto.driver_py, "generic driver must be non-empty"
    assert proto.monitor_py, "generic monitor must be non-empty"
    # Scoreboard is sample-only (no comparison), but non-empty.
    assert proto.scoreboard_py


def test_generic_protocol_drives_all_inputs():
    d = _make_unknown_design()
    classify(d)
    proto = for_design(d)
    # io_in0 / io_in1 should appear as inputs the driver toggles.
    for nm in ("io_in0", "io_in1"):
        assert nm in proto.driver_py, f"{nm} must be driven in generic mode"


def test_generic_fallback_has_skeleton_with_drivers(tmp_path: Path):
    """Skeleton gate (spec §108) requires drivers/monitors — generic fallback must satisfy."""
    d = _make_unknown_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=1)
    out = tmp_path / "case_unknown"
    res = render(d, constraints, bins, out)
    skeleton = res.skeleton
    assert skeleton["drivers"], "drivers list must be non-empty (gate requirement)"
    assert skeleton["monitors"], "monitors list must be non-empty (gate requirement)"
    assert skeleton["scoreboard"]


def test_generic_fallback_generates_real_testbench(tmp_path: Path):
    """Plan A: with no protocol detected, generated_tb/ must still get real files."""
    d = _make_unknown_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=1)
    out = tmp_path / "case_unknown"
    res = render(d, constraints, bins, out)
    assert (res.tb_dir / "tb_top.py").exists()
    assert (res.tb_dir / "dut_inst.v").exists()
    assert (res.tb_dir / "Makefile").exists()
    text = (res.tb_dir / "tb_top.py").read_text(encoding="utf-8")
    assert "generic_driver" in text
    assert "GenericScoreboard" in text
    assert "io_in0" in text or "io_out0" in text


def test_generic_fallback_has_cp_generic_run_bin():
    d = _make_unknown_design()
    classify(d)
    bins = define(d)
    cp_names = {cp["name"] for cp in bins["coverpoints"]}
    assert "cp_generic_run" in cp_names
    # And the bin must have explicit sampling_condition.
    for cp in bins["coverpoints"]:
        for b in cp["bins"]:
            assert b.get("sampling_condition"), f"{cp['name']}/{b.get('name')} missing"


def test_generic_protocol_registry_fallback():
    """PROTOCOL_REGISTRY must route unknown protocols to the generic generator (not to nothing)."""
    from verif_agent.tb_gen.protocols.base import ProtocolOutput
    fn = PROTOCOL_REGISTRY.get("", PROTOCOL_REGISTRY.get("passive"))
    assert fn is not None
    # Calling it should return a non-empty ProtocolOutput.
    out = fn(_make_unknown_design())
    assert isinstance(out, ProtocolOutput)
    assert out.driver_py
