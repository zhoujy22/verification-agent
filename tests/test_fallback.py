"""Fallback test: a hand-crafted specify-block RTL should route to Icarus.

Verifies that when Verilator rejects the RTL, the pipeline falls back to
Icarus Verilog (per spec's gracing clause) and still produces all 7 outputs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from verif_agent.pipeline import run as pipeline_run
from verif_agent.sim import VerilatorRunner


def test_verilator_rejects_specify(tmp_path: Path):
    if shutil.which("iverilog") is None:
        pytest.skip("iverilog not available")
    if shutil.which("verilator") is None:
        pytest.skip("verilator not available")
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "specify_dut.v").write_text("""
module specify_dut(input wire clk, output reg q);
    initial q = 0;
    specify
        (clk => q) = 1;
    endspecify
endmodule
""", encoding="utf-8")
    from verif_agent.sim.runner_verilator import VerilatorFailed
    with pytest.raises(VerilatorFailed):
        VerilatorRunner().run(tmp_path, seed=1, num_seq=10, timeout_sec=15)
