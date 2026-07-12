"""Clock/reset snippet generation. Outputs Python string for cocotb Clock + reset routine."""
from __future__ import annotations

from ..design import Design


def render(design: Design) -> str:
    """Return Python source for clock + reset block. Picks first clock/reset from Design."""
    if not design.clock:
        return """
async def setup_clock_reset(dut):
    if not hasattr(dut, 'clk'):
        return
    cocotb.start_soon(Clock(dut.clk, 10, units='ns').start())
    for _ in range(5):
        await RisingEdge(dut.clk)
"""
    clk_name = design.clock[0].name
    period = design.clock[0].period_ns
    rst_block_lines = []
    if design.reset:
        rst_name = design.reset[0].name
        active = design.reset[0].active_level
        cycles = design.reset[0].duration_cycles
        rst_block_lines.append(f"    dut.{rst_name}.value = {active}")
        rst_block_lines.append(f"    for _ in range({cycles}):")
        rst_block_lines.append(f"        await RisingEdge(dut.{clk_name})")
        rst_block_lines.append(f"    dut.{rst_name}.value = {1 - active}")
    else:
        rst_block_lines.append("    # no reset signal detected")
        rst_block_lines.append(f"    for _ in range(3):")
        rst_block_lines.append(f"        await RisingEdge(dut.{clk_name})")

    return f"""
async def setup_clock_reset(dut):
    cocotb.start_soon(Clock(dut.{clk_name}, {period}, units='ns').start())
{chr(10).join(rst_block_lines)}
"""
