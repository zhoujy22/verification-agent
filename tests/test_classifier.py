"""Tests for the protocol classifier."""
from __future__ import annotations

from verif_agent.classifier import classify
from verif_agent.design import Design, Port


def _make_design(ports: list[tuple[str, str, int]], top="top") -> Design:
    return Design(
        top=top,
        ports=[Port(name=n, direction=d, width=w) for n, d, w in ports],
    )


def _groups(design: Design) -> dict[str, str]:
    return {p.name: p.protocol_group for p in design.ports}


def _roles(design: Design) -> dict[str, str]:
    return {p.name: p.role for p in design.ports}


def test_classify_axi_lite():
    d = _make_design([
        ("aclk", "input", 1),
        ("aresetn", "input", 1),
        ("awaddr", "input", 32), ("awvalid", "input", 1), ("awready", "output", 1),
        ("wdata", "input", 32), ("wvalid", "input", 1), ("wready", "output", 1),
        ("wstrb", "input", 4),
        ("bresp", "output", 2), ("bvalid", "output", 1), ("bready", "input", 1),
        ("araddr", "input", 32), ("arvalid", "input", 1), ("arready", "output", 1),
        ("rdata", "output", 32), ("rresp", "output", 2), ("rvalid", "output", 1), ("rready", "input", 1),
    ])
    classify(d)
    assert d.primary_protocol == "AXI4-Lite"
    assert "AXI4-Lite" in d.inferred_protocols
    groups = _groups(d)
    assert groups["aclk"] == "clk"
    assert groups["aresetn"] == "rst"
    assert groups["awaddr"] == "axi_aw"
    assert groups["rdata"] == "axi_r"
    roles = _roles(d)
    assert roles["awaddr"] == "driver"
    assert roles["rdata"] == "monitor"


def test_classify_axi_full():
    d = _make_design([
        ("aclk", "input", 1), ("aresetn", "input", 1),
        ("awaddr", "input", 32), ("awvalid", "input", 1), ("awready", "output", 1),
        ("awburst", "input", 2), ("awlen", "input", 8), ("awsize", "input", 3),
        ("wdata", "input", 32), ("wvalid", "input", 1), ("wready", "output", 1),
        ("bresp", "output", 2), ("bvalid", "output", 1), ("bready", "input", 1),
        ("araddr", "input", 32), ("arvalid", "input", 1), ("arready", "output", 1),
        ("rdata", "output", 32), ("rresp", "output", 2), ("rvalid", "output", 1), ("rready", "input", 1),
    ])
    classify(d)
    assert d.primary_protocol == "AXI4"
    assert "AXI4" in d.inferred_protocols


def test_classify_sram():
    d = _make_design([
        ("clk", "input", 1), ("rst_n", "input", 1),
        ("csb", "input", 1), ("we", "input", 1),
        ("addr", "input", 16), ("din", "input", 8), ("dout", "output", 8),
    ])
    classify(d)
    assert "SRAM" in d.inferred_protocols
    assert d.primary_protocol == "SRAM"
    groups = _groups(d)
    assert groups["csb"] == "sram"
    assert groups["we"] == "sram"
    assert groups["addr"] == "sram"
    assert groups["din"] == "sram"
    assert groups["dout"] == "sram"
    roles = _roles(d)
    assert roles["addr"] == "driver"
    assert roles["dout"] == "monitor"


def test_classify_stream():
    d = _make_design([
        ("clk", "input", 1), ("rst_n", "input", 1),
        ("in_valid", "input", 1), ("in_ready", "output", 1), ("in_data", "input", 8),
        ("out_valid", "output", 1), ("out_ready", "input", 1), ("out_data", "output", 8),
    ])
    classify(d)
    assert "valid_ready_stream" in d.inferred_protocols
    assert d.primary_protocol == "valid_ready_stream"
    groups = _groups(d)
    assert groups["in_valid"] == "stream_in"
    assert groups["out_valid"] == "stream_out"
    assert groups["in_data"] == "stream_in"
    assert groups["out_data"] == "stream_out"
    roles = _roles(d)
    assert roles["in_valid"] == "driver"
    assert roles["out_ready"] == "driver"   # we drive out_ready (backpressure)
    assert roles["out_valid"] == "monitor"
    assert roles["in_ready"] == "monitor"


def test_classify_passive():
    d = _make_design([
        ("clk", "input", 1), ("rst_n", "input", 1),
        ("gpio_in", "input", 8), ("gpio_out", "output", 8),
    ])
    classify(d)
    assert d.primary_protocol == ""
    assert d.inferred_protocols == []
    groups = _groups(d)
    assert groups["gpio_in"] == "passive"
    assert groups["gpio_out"] == "passive"


def test_classify_apb():
    d = _make_design([
        ("pclk", "input", 1), ("presetn", "input", 1),
        ("psel", "input", 1), ("penable", "input", 1), ("pwrite", "input", 1),
        ("paddr", "input", 32), ("pwdata", "input", 32),
        ("prdata", "output", 32), ("pready", "output", 1),
    ])
    classify(d)
    assert "APB" in d.inferred_protocols
    assert d.primary_protocol == "APB"
    groups = _groups(d)
    assert groups["psel"] == "apb"
    assert groups["prdata"] == "apb"
    assert groups["pclk"] == "clk"


def test_classify_apb_with_prefix():
    """APB signals with `m_apb_` / `s_apb_` prefix must still be recognized."""
    d = _make_design([
        ("pclk", "input", 1),
        ("m_apb_psel", "input", 1), ("m_apb_penable", "input", 1),
        ("m_apb_pwrite", "input", 1), ("m_apb_paddr", "input", 16),
        ("m_apb_pwdata", "input", 32),
        ("s_apb_prdata", "output", 32), ("s_apb_pready", "output", 1),
    ])
    classify(d)
    assert "APB" in d.inferred_protocols
    groups = _groups(d)
    assert groups["m_apb_psel"] == "apb"
    assert groups["s_apb_prdata"] == "apb"


def test_classify_axi_with_master_prefix():
    """AXI signals with `m_axi_*` prefix must still be recognized as AXI4-Lite."""
    d = _make_design([
        ("aclk", "input", 1), ("aresetn", "input", 1),
        ("m_axi_awaddr", "input", 32), ("m_axi_awvalid", "input", 1),
        ("m_axi_awready", "output", 1),
        ("m_axi_wdata", "input", 32), ("m_axi_wvalid", "input", 1),
        ("m_axi_wready", "output", 1), ("m_axi_wstrb", "input", 4),
        ("m_axi_bresp", "output", 2), ("m_axi_bvalid", "output", 1),
        ("m_axi_bready", "input", 1),
        ("m_axi_araddr", "input", 32), ("m_axi_arvalid", "input", 1),
        ("m_axi_arready", "output", 1),
        ("m_axi_rdata", "output", 32), ("m_axi_rresp", "output", 2),
        ("m_axi_rvalid", "output", 1), ("m_axi_rready", "input", 1),
    ])
    classify(d)
    assert d.primary_protocol == "AXI4-Lite"
    assert "AXI4-Lite" in d.inferred_protocols
    groups = _groups(d)
    assert groups["m_axi_awaddr"] == "axi_aw"
    assert groups["m_axi_rdata"] == "axi_r"


def test_classify_apb_takes_precedence_over_stream():
    """When APB signals coexist with valid/ready, APB is the recognized protocol."""
    d = _make_design([
        ("pclk", "input", 1),
        ("psel", "input", 1), ("penable", "input", 1), ("pwrite", "input", 1),
        ("paddr", "input", 32), ("pwdata", "input", 32),
        ("prdata", "output", 32), ("pready", "output", 1),
        ("in_valid", "input", 1), ("in_ready", "output", 1), ("in_data", "input", 8),
        ("out_valid", "output", 1), ("out_ready", "input", 1), ("out_data", "output", 8),
    ])
    classify(d)
    # Both protocols recognized; AXI/APB come before stream in the protocol order
    assert "APB" in d.inferred_protocols
    assert "valid_ready_stream" in d.inferred_protocols
    # primary follows ordering: AXI4/AXI4-Lite > APB > SRAM > valid_ready_stream
    assert d.primary_protocol == "APB"
