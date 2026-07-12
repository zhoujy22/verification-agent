"""SRAM-like protocol generator.

Drives csb/we/addr/din, monitors dout, scoreboard is dict-backed memory.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def generate(design: Design) -> ProtocolOutput:
    ports = {p.name: p for p in design.ports}
    csb = next((n for n in ("csb", "cs_n", "cen", "cs") if n in ports), "csb")
    we = next((n for n in ("we", "we_n", "web") if n in ports), "we")
    addr = "addr" if "addr" in ports else "ADDR"
    din = "din" if "din" in ports else "DIN"
    dout = "dout" if "dout" in ports else "DOUT"
    wmask = "wmask" if "wmask" in ports else "WMASK"

    driver_py = f'''
async def sram_driver(dut, rng, num_seq):
    for i in range(num_seq):
        await RisingEdge(dut.clk)
        dut.{csb}.value = 0
        dut.{we}.value = rng.randint(0, 1)
        dut.{addr}.value = rng.randint(0, (1 << 16) - 1)
        if int(dut.{we}.value) == 1:
            dut.{din}.value = rng.randint(0, (1 << 8) - 1)
            try:
                dut.{wmask}.value = rng.randint(0, 0xF) or 0xF
            except Exception:
                pass
        # short hold
        for _ in range(rng.randint(1, 3)):
            await RisingEdge(dut.clk)
        dut.{csb}.value = 1
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.{csb}.value = 1
'''

    monitor_py = f'''
async def sram_monitor(dut, log_q: list, coverpoint_sampler=None):
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.{csb}.value) == 0:
            entry = {{
                "csb": 0,
                "we": int(dut.{we}.value),
                "addr": int(dut.{addr}.value),
                "din": int(dut.{din}.value),
                "dout": int(dut.{dout}.value) if "dout" in dir(dut) else 0,
            }}
            log_q.append(entry)
            if coverpoint_sampler is not None:
                coverpoint_sampler("sram", entry)
'''

    scoreboard_py = '''
class SramScoreboard:
    """Address-keyed memory model. Writes update; reads must match stored."""
    def __init__(self, log_q: list):
        self.log = log_q
        self.mem: dict = {}
        self.failures = []

    def check(self):
        for e in self.log:
            if e["we"] == 1:
                self.mem[e["addr"]] = e["din"]
            else:
                # read
                if e["addr"] in self.mem:
                    if e["dout"] != self.mem[e["addr"]]:
                        if len(self.failures) < 50:
                            self.failures.append(
                                f"SRAM_READ_MISMATCH@addr={{e['addr']}} expected={{self.mem[e['addr']]}} got={{e['dout']}}"
                            )
                        return False
                # If never written, anything is OK (don't care)
        return True
'''

    coverpoint_py = f'''
def _sample_sram_bins(dut, cp_registry):
    try:
        c = int(dut.{csb}.value)
        w = int(dut.{we}.value)
        a = int(dut.{addr}.value)
    except Exception:
        return
    if c != 0:
        return
    if "cp_addr_align" in cp_registry:
        cp = cp_registry["cp_addr_align"]
        if (a & 0x3) == 0:
            cp["BIN_ALIGNED"].hit += 1
        else:
            cp["BIN_MISALIGNED"].hit += 1
    if "cp_we" in cp_registry:
        cp = cp_registry["cp_we"]
        if w == 0:
            cp["BIN_READ"].hit += 1
        else:
            cp["BIN_WRITE"].hit += 1
    if "cp_wmask" in cp_registry:
        cp = cp_registry["cp_wmask"]
        if w == 1:
            try:
                m = int(dut.{wmask}.value)
                if m == 0xF:
                    cp["BIN_FULL"].hit += 1
                elif 0 < m < 0xF:
                    cp["BIN_PARTIAL"].hit += 1
            except Exception:
                pass
'''

    ports_handled = [p.name for p in design.ports
                     if p.protocol_group in {"sram", "clk", "rst"}]

    return ProtocolOutput(
        name="sram",
        handshake="level",
        backpressure_strategy="none",
        driver_name="sram_driver",
        monitor_name="sram_monitor",
        scoreboard_name="SramScoreboard",
        scoreboard_checks=["read_matches_stored_value"],
        driver_py=driver_py,
        monitor_py=monitor_py,
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="sram",
    )
