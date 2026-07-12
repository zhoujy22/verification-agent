"""Tests for the RTL parser layer.

Coverage:
  - ANSI port list
  - non-ANSI port list with body declarations
  - multi-file RTL with `include
  - PyVerilog failure → falls back to regex (regex parser still emits ports)
  - regression: design.json.port list is never empty on sane Verilog
"""
from __future__ import annotations

from pathlib import Path

import pytest

from verif_agent.rtl_parser import discover_rtl_files, resolve
from verif_agent.rtl_parser.regex_parser import parse as rx_parse


AXI_LITE_ANSI = """
module axi_lite_dut
  #(parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32)
   (
    input  wire                    aclk,
    input  wire                    aresetn,
    input  wire [ADDR_WIDTH-1:0]   awaddr,
    input  wire                    awvalid,
    output wire                    awready,
    input  wire [DATA_WIDTH-1:0]   wdata,
    input  wire                    wvalid,
    output wire                    wready,
    output wire [1:0]              bresp,
    output wire                    bvalid,
    input  wire                    bready,
    input  wire [ADDR_WIDTH-1:0]   araddr,
    input  wire                    arvalid,
    output wire                    arready,
    output wire [DATA_WIDTH-1:0]   rdata,
    output wire [1:0]              rresp,
    output wire                    rvalid,
    input  wire                    rready
   );
  // body omitted
endmodule
"""


NON_ANSI = """
module non_ansi_dut (data_in, data_out, clk, rst_n);
  parameter WIDTH = 8;
  input  wire          clk;
  input  wire          rst_n;
  input  wire [WIDTH-1:0] data_in;
  output wire [WIDTH-1:0] data_out;
endmodule
"""


MULTI_FILE_RTL = """
// --- dut.v ---
module multi_dut #(parameter WIDTH=8) (
    input              clk,
    input              rst_n,
    input  [WIDTH-1:0] in_data,
    output [WIDTH-1:0] out_data,
    input              in_valid,
    output             in_ready,
    output             out_valid,
    input              out_ready
);
`include "helper.vh"
endmodule

// --- helper.vh ---
// helpers, no module
"""


def test_axi_lite_basic():
    parsed = rx_parse(AXI_LITE_ANSI)
    assert parsed.module_name == "axi_lite_dut"
    names = [it.name for it in parsed.items if not it.param]
    assert "aclk" in names
    assert "awaddr" in names
    # width_expr should be captured for parameterized widths.
    by_name = {it.name: it for it in parsed.items if not it.param}
    assert by_name["aclk"].width == 1
    assert by_name["aclk"].width_expr is None
    assert by_name["awaddr"].width == 1   # raw; resolved downstream
    assert by_name["awaddr"].width_expr == "ADDR_WIDTH-1:0"


def test_axi_lite_widths_resolved_via_resolver(tmp_path: Path):
    """End-to-end via port_resolver: parameterized widths get resolved."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "dut.v").write_text(AXI_LITE_ANSI)
    design = resolve(rtl, "axi_lite_dut")
    widths = {p.name: p.width for p in design.ports}
    assert widths["aclk"] == 1
    assert widths["awaddr"] == 32
    assert widths["wdata"] == 32


def test_regex_handles_ansi():
    parsed = rx_parse(AXI_LITE_ANSI)
    directions = {it.name: it.direction for it in parsed.items if not it.param}
    assert directions["aclk"] == "input"
    assert directions["awaddr"] == "input"
    assert directions["awready"] == "output"
    # rdata isn't in this shorter snippet; check awready direction instead.
    assert directions["awready"] == "output"


def test_regex_handles_nonansi():
    parsed = rx_parse(NON_ANSI)
    assert parsed.module_name == "non_ansi_dut"
    items = {it.name: it for it in parsed.items if not it.param}
    assert items["clk"].direction == "input"
    assert items["rst_n"].direction == "input"
    assert items["data_in"].direction == "input"
    # raw width=1; resolution happens in port_resolver.
    assert items["data_in"].width_expr == "WIDTH-1:0"
    assert items["data_out"].direction == "output"


def test_nonansi_widths_resolved_via_resolver(tmp_path: Path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "dut.v").write_text(NON_ANSI)
    design = resolve(rtl, "non_ansi_dut")
    widths = {p.name: p.width for p in design.ports}
    assert widths["data_in"] == 8
    assert widths["data_out"] == 8


def test_include_resolution(tmp_path: Path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "dut.v").write_text(MULTI_FILE_RTL)
    design = resolve(rtl, "multi_dut")
    port_names = {p.name for p in design.ports}
    assert {"clk", "rst_n", "in_data", "out_data", "in_valid", "in_ready",
            "out_valid", "out_ready"} <= port_names


def test_pyverilog_failure_falls_back_to_regex(tmp_path: Path):
    """A weird construct may break PyVerilog; regex must still emit ports."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    weird = """
    module dut(input clk, input rst_n, input [3:0] data, output [3:0] q);
        // `ifdef SOMETHING_NOT_REAL — left in place by preprocessor
        `ifdef SOMETHING_NOT_REAL
           garbage_block;
        `endif
        assign q = data;
    endmodule
    """
    (rtl / "dut.v").write_text(weird)
    design = resolve(rtl, "dut")
    names = {p.name for p in design.ports}
    assert {"clk", "rst_n", "data", "q"} <= names
    assert len(names) >= 4


def test_discover_rtl_files(tmp_path: Path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "a.v").write_text("// empty")
    (rtl / "b.sv").write_text("// empty")
    (rtl / "c.txt").write_text("ignored")
    files = discover_rtl_files(rtl)
    names = {Path(f).name for f in files}
    assert {"a.v", "b.sv"} <= names
    assert "c.txt" not in names


def test_design_dataclass_round_trip():
    """Basic structural round-trip — design.to_dict() should produce required keys."""
    design = resolve  # type: ignore[func-annotation]  # touch import
    from verif_agent.rtl_parser.port_resolver import resolve as _resolve

    import tempfile, textwrap
    with tempfile.TemporaryDirectory() as d:
        Path(d, "rtl").mkdir()
        Path(d, "rtl", "a.v").write_text(textwrap.dedent("""
            module dut(input clk, input rst_n, input [7:0] d, output [7:0] q);
                assign q = d;
            endmodule
        """))
        design = _resolve(Path(d) / "rtl", "dut")
    d_dict = design.to_dict()
    assert d_dict["top"] == "dut"
    assert isinstance(d_dict["ports"], list) and d_dict["ports"]
    for p in d_dict["ports"]:
        assert {"name", "direction", "width"} <= p.keys()
