"""Verify verification_skeleton.json names match the actual identifiers in
generated_tb/tb_top.py — drift detector for the rename bug we just fixed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from verif_agent.classifier import classify
from verif_agent.constraints_gen import generate
from verif_agent.coverage_definer import define
from verif_agent.design import Design, Port
from verif_agent.tb_gen.render import render


def _stream_design() -> Design:
    return Design(top="t", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="in_valid", direction="input", width=1),
        Port(name="in_ready", direction="output", width=1),
        Port(name="in_data", direction="input", width=8),
        Port(name="out_valid", direction="output", width=1),
        Port(name="out_ready", direction="input", width=1),
        Port(name="out_data", direction="output", width=8),
    ])


def _axi_lite_design() -> Design:
    ports = [
        ("aclk", "input", 1), ("aresetn", "input", 1),
        ("awaddr", "input", 32), ("awvalid", "input", 1), ("awready", "output", 1),
        ("wdata", "input", 32), ("wstrb", "input", 4),
        ("wvalid", "input", 1), ("wready", "output", 1),
        ("bresp", "output", 2), ("bvalid", "output", 1), ("bready", "input", 1),
        ("araddr", "input", 32), ("arvalid", "input", 1), ("arready", "output", 1),
        ("rdata", "output", 32), ("rresp", "output", 2),
        ("rvalid", "output", 1), ("rready", "input", 1),
    ]
    return Design(top="t", ports=[Port(name=n, direction=d, width=w) for n, d, w in ports])


def _identifiers_in_source(source: str) -> set[str]:
    """Extract Python function/class/identifier definitions from generated source."""
    out = set(re.findall(r"^class\s+(\w+)", source, flags=re.MULTILINE))
    out |= set(re.findall(r"^async def\s+(\w+)", source, flags=re.MULTILINE))
    out |= set(re.findall(r"^def\s+(\w+)", source, flags=re.MULTILINE))
    return out


def _render_and_assert(tmp_path: Path, design: Design, *,
                      expected_driver: str, expected_monitor: str,
                      expected_scoreboard: str, expected_checks: list[str]):
    classify(design)
    bins = define(design)
    constraints = generate(design, seed=1)
    res = render(design, constraints, bins, tmp_path / "case")

    # Read skeleton and tb_top.py
    skeleton = res.skeleton
    tb_text = (res.tb_dir / "tb_top.py").read_text(encoding="utf-8")
    identifiers = _identifiers_in_source(tb_text)

    # Names declared in skeleton must exist verbatim in tb_top.py
    assert skeleton["drivers"][0]["name"] == expected_driver
    assert expected_driver in identifiers, f"{expected_driver} not defined in tb_top.py"

    assert skeleton["monitors"][0]["name"] == expected_monitor
    assert expected_monitor in identifiers, f"{expected_monitor} not defined in tb_top.py"

    assert skeleton["scoreboard"]["name"] == expected_scoreboard
    assert expected_scoreboard in identifiers, f"{expected_scoreboard} not defined in tb_top.py"

    assert skeleton["scoreboard"]["checks"] == expected_checks


def test_stream_skeleton_matches_code(tmp_path: Path):
    d = _stream_design()
    _render_and_assert(tmp_path, d,
                       expected_driver="stream_driver",
                       expected_monitor="stream_monitor",
                       expected_scoreboard="StreamScoreboard",
                       expected_checks=["fifo_order", "data_integrity"])


def test_axi_lite_skeleton_matches_code(tmp_path: Path):
    d = _axi_lite_design()
    _render_and_assert(tmp_path, d,
                       expected_driver="axi_lite_driver",
                       expected_monitor="axi_lite_monitor",
                       expected_scoreboard="AxiLiteScoreboard",
                       expected_checks=["bresp_okay", "rresp_okay"])


def test_sram_skeleton_matches_code(tmp_path: Path):
    d = Design(top="t", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="csb", direction="input", width=1),
        Port(name="we", direction="input", width=1),
        Port(name="addr", direction="input", width=16),
        Port(name="din", direction="input", width=8),
        Port(name="dout", direction="output", width=8),
    ])
    _render_and_assert(tmp_path, d,
                       expected_driver="sram_driver",
                       expected_monitor="sram_monitor",
                       expected_scoreboard="SramScoreboard",
                       expected_checks=["read_matches_stored_value"])


def test_apb_skeleton_matches_code(tmp_path: Path):
    d = Design(top="t", ports=[
        Port(name="pclk", direction="input", width=1),
        Port(name="psel", direction="input", width=1),
        Port(name="penable", direction="input", width=1),
        Port(name="pwrite", direction="input", width=1),
        Port(name="paddr", direction="input", width=16),
        Port(name="pwdata", direction="input", width=32),
        Port(name="prdata", direction="output", width=32),
        Port(name="pready", direction="output", width=1),
    ])
    _render_and_assert(tmp_path, d,
                       expected_driver="apb_driver",
                       expected_monitor="apb_monitor",
                       expected_scoreboard="ApbScoreboard",
                       expected_checks=["prdata_matches_stored_value", "pslverr_zero"])


def test_generic_fallback_skeleton_matches_code(tmp_path: Path):
    """Plan A: unknown protocol → generic fallback; names must still align."""
    d = Design(top="t", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="io_in", direction="input", width=8),
        Port(name="io_out", direction="output", width=8),
    ])
    _render_and_assert(tmp_path, d,
                       expected_driver="generic_driver",
                       expected_monitor="generic_monitor",
                       expected_scoreboard="GenericScoreboard",
                       expected_checks=["at_least_one_sample_captured"])


def test_no_refmodel_suffix_anywhere(tmp_path: Path):
    """Drift detector: the old bogus `_refmodel` suffix must not appear anywhere."""
    d = _stream_design()
    classify(d)
    bins = define(d)
    constraints = generate(d, seed=1)
    out = tmp_path / "x_case"
    res = render(d, constraints, bins, out)

    skel_text = json.dumps(res.skeleton, ensure_ascii=False)
    tb_text = (res.tb_dir / "tb_top.py").read_text(encoding="utf-8")

    # Strip the path prefix before checking, so test names containing "_refmodel"
    # don't trip the detector (this test is named after the bug itself).
    skel_text_safe = skel_text.replace(str(out), "")
    skel_text_safe = skel_text_safe.replace(str(tmp_path), "")
    tb_text_safe = tb_text.replace(str(out), "")
    tb_text_safe = tb_text_safe.replace(str(tmp_path), "")

    # Drift tokens we never want to see generated. These were the old broken
    # auto-generated names from before this fix landed.
    for forbidden in ("valid_ready_stream_driver", "valid_ready_stream_monitor",
                       "valid_ready_stream_refmodel",
                       "axi_lite_refmodel", "sram_refmodel", "apb_refmodel"):
        assert forbidden not in skel_text_safe, f"forbidden token {forbidden!r} still in skeleton"
        assert forbidden not in tb_text_safe, f"forbidden token {forbidden!r} still in tb_top.py"
