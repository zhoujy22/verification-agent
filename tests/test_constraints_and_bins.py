"""Tests for constraints_gen and coverage_definer."""
from __future__ import annotations

import pytest

from verif_agent.classifier import classify
from verif_agent.constraints_gen import generate
from verif_agent.coverage_definer import define
from verif_agent.design import Design, Port


def _axi_full_design() -> Design:
    ports = [
        Port(name="aclk", direction="input", width=1),
        Port(name="aresetn", direction="input", width=1),
        Port(name="awaddr", direction="input", width=32),
        Port(name="awvalid", direction="input", width=1),
        Port(name="awready", direction="output", width=1),
        Port(name="awburst", direction="input", width=2),
        Port(name="awsize", direction="input", width=3),
        Port(name="awlen", direction="input", width=8),
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


def test_constraints_basics():
    d = _axi_full_design()
    classify(d)
    c = generate(d, seed=42, num_seq=5000)
    assert c["seed"] == 42
    assert c["num_seq"] == 5000
    assert c["coverage_feedback_updates"] == []
    var_names = {v["name"] for v in c["random_variables"]}
    # driver-side AXI signals should be present
    for must in ("awaddr", "wdata", "wstrb", "araddr"):
        assert must in var_names


def test_constraints_wstrb_has_weights():
    d = _axi_full_design()
    classify(d)
    c = generate(d, seed=1)
    w = next(v for v in c["random_variables"] if v["name"] == "wstrb")
    assert w["dist"] == "weighted"
    assert "0xF" in w["weights"]


def test_coverage_bins_have_sampling_condition():
    """Spec §34: each bin MUST have an explicit sampling_condition."""
    d = _axi_full_design()
    classify(d)
    bins = define(d)
    for cp in bins["coverpoints"]:
        for b in cp["bins"]:
            assert b.get("sampling_condition"), f"{cp['name']}/{b.get('name')} missing sampling_condition"
            assert b.get("scenario"), f"{cp['name']}/{b.get('name')} missing scenario"
            assert "hit_count" in b
            assert "covered" in b


def test_coverage_bins_stream():
    d = Design(top="stream_dut", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="in_valid", direction="input", width=1),
        Port(name="in_ready", direction="output", width=1),
        Port(name="in_data", direction="input", width=8),
        Port(name="out_valid", direction="output", width=1),
        Port(name="out_ready", direction="input", width=1),
        Port(name="out_data", direction="output", width=8),
    ])
    classify(d)
    bins = define(d)
    cp_names = {cp["name"] for cp in bins["coverpoints"]}
    assert {"cp_payload", "cp_backpressure", "cp_idle"} <= cp_names


def test_coverage_bins_rejects_missing_condition():
    d = Design(top="x", ports=[Port(name="clk", direction="input", width=1)])
    classify(d)
    # Define with a missing sampling condition — should raise
    from verif_agent.coverage_definer import _enforce_sampling_condition
    with pytest.raises(ValueError):
        _enforce_sampling_condition({"name": "BAD_BIN", "scenario": "x"})
