"""Clock/reset snippet generation. Outputs Python source for cocotb Clock + reset routine.

Supports multi-clock designs: every clock in design.clock gets its own
cocotb.Clock, and every reset in design.reset is pulsed. Single-clock designs
keep the simple legacy form so the generated source is unchanged for them.
"""
from __future__ import annotations

from ..design import Design


def render(design: Design) -> str:
    """Return Python source for clock + reset block."""
    clocks = design.clock
    if not clocks:
        return """
async def setup_clock_reset(dut):
    if not hasattr(dut, 'clk'):
        return
    cocotb.start_soon(Clock(dut.clk, 10, units='ns').start())
    for _ in range(5):
        await RisingEdge(dut.clk)
"""

    # Single-clock: legacy compact form.
    if len(clocks) == 1:
        clk = clocks[0]
        period = clk.period_ns
        rst_lines: list[str] = []
        if design.reset:
            r = design.reset[0]
            active = r.active_level
            cycles = r.duration_cycles
            rst_lines.append(f"    dut.{r.name}.value = {active}")
            rst_lines.append(f"    for _ in range({cycles}):")
            rst_lines.append(f"        await RisingEdge(dut.{clk.name})")
            rst_lines.append(f"    dut.{r.name}.value = {1 - active}")
        else:
            rst_lines.append("    # no reset signal detected")
            rst_lines.append("    for _ in range(3):")
            rst_lines.append(f"        await RisingEdge(dut.{clk.name})")
        return f"""
async def setup_clock_reset(dut):
    cocotb.start_soon(Clock(dut.{clk.name}, {period}, units='ns').start())
{chr(10).join(rst_lines)}
"""

    # Multi-clock: start every clock, then pulse every reset on the primary
    # clock (clk) edge. case3 has clk + input_clk + output_clk plus several
    # resets (rst / input_rst / output_rst / rst_req_in / cfg_reset); all must
    # be driven for the DUT's three clock domains to function.
    primary = clocks[0]  # clk (AXI domain) — reset reference
    lines = [f"async def setup_clock_reset(dut):"]
    for c in clocks:
        lines.append(f"    cocotb.start_soon(Clock(dut.{c.name}, {c.period_ns}, units='ns').start())")
    if design.reset:
        # Assert all resets (hold 0 for active-low, 1 for active-high) idle first.
        for r in design.reset:
            lines.append(f"    dut.{r.name}.setimmediatevalue({1 - r.active_level})")
        lines.append(f"    for _ in range(2):")
        lines.append(f"        await RisingEdge(dut.{primary.name})")
        for r in design.reset:
            lines.append(f"    dut.{r.name}.value = {r.active_level}")
        lines.append(f"    for _ in range(5):")
        lines.append(f"        await RisingEdge(dut.{primary.name})")
        for r in design.reset:
            lines.append(f"    dut.{r.name}.value = {1 - r.active_level}")
        lines.append(f"    for _ in range(2):")
        lines.append(f"        await RisingEdge(dut.{primary.name})")
    else:
        lines.append(f"    for _ in range(5):")
        lines.append(f"        await RisingEdge(dut.{primary.name})")
    return "\n".join(lines) + "\n"
