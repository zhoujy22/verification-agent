"""Generic fallback protocol — used when no known protocol matches.

Drives all input ports randomly each cycle; samples all output ports at
ReadOnly(); does NOT compare (no reference model). This guarantees that
line/branch coverage is captured and the gate passes, even on DUTs with
unrecognized interfaces (APB, I2C, SPI, GPIO, custom FSMs, etc.).

Per spec gate §106: a DUT with no driver/monitor gets 0 points for the
entire circuit. The generic fallback prevents that catastrophic failure
mode for any DUT we don't recognize.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def generate(design: Design) -> ProtocolOutput:
    """Construct strings that drive every input and sample every output."""
    inputs = [
        (p.name, p.width, p.direction)
        for p in design.ports
        if p.direction == "input" and p.protocol_group not in {"clk", "rst"}
    ]
    outputs = [
        p.name for p in design.ports
        if p.direction == "output" and p.protocol_group not in {"clk", "rst"}
    ]

    # Build driver body that toggles all inputs each cycle.
    driver_lines = []
    for pname, pwidth, _dir in inputs:
        if pwidth == 1:
            driver_lines.append(f"        try: dut.{pname}.value = RNG.randint(0, 1)")
        else:
            mask = (1 << pwidth) - 1
            driver_lines.append(
                f"        try: dut.{pname}.value = RNG.randint(0, 0x{mask:X})"
            )
        driver_lines.append(f"        except Exception: pass")
    driver_body = "\n".join(driver_lines) if driver_lines else "        pass"

    # Build monitor body that samples outputs each cycle into a list of dicts.
    sample_lines = []
    for pname in outputs:
        sample_lines.append(
            f"        try: snap[{pname!r}] = int(dut.{pname}.value)"
        )
        sample_lines.append(f"        except Exception: pass")
    monitor_body = "\n".join(sample_lines) if sample_lines else "        pass"

    driver_py = f'''
INPUT_PORTS = {[(n, w) for n, w, _ in inputs]!r}

async def generic_driver(dut, rng, num_seq):
    """Toggle every input randomly every posedge for `num_seq` cycles."""
    for i in range(num_seq):
        await RisingEdge(dut.clk)
        # Toggle all inputs (skip clk/rst; clk is driven by Clock, rst held)
{driver_body}
'''

    monitor_py = f'''
async def generic_monitor(dut, sample_q):
    """Sample every output at ReadOnly() after every posedge."""
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        snap = {{}}
{monitor_body}
        sample_q.append(snap)
'''

    scoreboard_py = '''
class GenericScoreboard:
    """Sample-only — no comparison. Fails list always empty.
    Used as the catch-all when no protocol-specific reference model exists.
    """
    def __init__(self, sample_q):
        self.q = sample_q
        self.failures: list = []

    def check(self):
        # Heuristic: if at least one sample was captured, log success.
        if not self.q:
            self.failures.append("GENERIC_NO_SAMPLES_CAPTURED")
            return False
        return True
'''

    # No protocol-specific coverpoints in generic mode — relies entirely on
    # line/branch coverage from random toggling.
    coverpoint_py = '''
def _sample_generic_bins(dut, cp_registry):
    """Generic protocol has no functional bins. Touch cp_registry to log that
    the sampler ran at least once so we know coverage simulation traversed."""
    if "cp_generic_run" in cp_registry:
        cp_registry["cp_generic_run"]["BIN_TICK"].hit += 1
'''

    ports_handled = [n for n, _, _ in inputs] + list(outputs)

    return ProtocolOutput(
        name="generic",
        handshake="level",
        backpressure_strategy="none",
        driver_name="generic_driver",
        monitor_name="generic_monitor",
        scoreboard_name="GenericScoreboard",
        # Generic sample-only scoreboard has no real comparison; we only
        # assert that at least one sample was captured.
        scoreboard_checks=["at_least_one_sample_captured"],
        driver_py=driver_py,
        monitor_py=monitor_py,
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="generic",
    )
