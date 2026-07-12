"""APB (Advanced Peripheral Bus) protocol generator.

Lightweight transaction-level driver/monitor/scoreboard for AMBA APB v2/v3.
Signals recognized (per spec §18 + the typical set):
  psel, penable, pwrite, paddr, pwdata, prdata, pready, pslverr, pstrb, pprot
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def generate(design: Design) -> ProtocolOutput:
    ports = {p.name: p for p in design.ports}

    psel    = next((n for n in ("psel",    "PSEL")    if n in ports), "psel")
    penable = next((n for n in ("penable", "PENABLE") if n in ports), "penable")
    pwrite  = next((n for n in ("pwrite",  "PWRITE")  if n in ports), "pwrite")
    paddr   = next((n for n in ("paddr",   "PADDR")   if n in ports), "paddr")
    pwdata  = next((n for n in ("pwdata",  "PWDATA")  if n in ports), "pwdata")
    prdata  = next((n for n in ("prdata",  "PRDATA")  if n in ports), "prdata")
    pready  = next((n for n in ("pready",  "PREADY")  if n in ports), "pready")
    pslverr = next((n for n in ("pslverr", "PSLVERR") if n in ports), "pslverr")
    pstrb   = next((n for n in ("pstrb",   "PSTRB")   if n in ports), "pstrb")

    driver_py = f'''
async def apb_driver(dut, rng, num_seq):
    """Drive `num_seq` APB transactions (write then read at same addr)."""
    for i in range(num_seq):
        addr = rng.randint(0, (1 << 32) - 1) & ~0x3
        wdata = rng.randint(0, (1 << 32) - 1)
        # WRITE: SETUP -> ENABLE -> wait pready
        await RisingEdge(dut.clk)
        dut.{psel}.value = 1
        dut.{penable}.value = 0
        dut.{pwrite}.value = 1
        dut.{paddr}.value = addr
        dut.{pwdata}.value = wdata
        try:
            dut.{pstrb}.value = rng.randint(1, 0xF)
        except Exception:
            pass
        await RisingEdge(dut.clk)
        dut.{penable}.value = 1
        # wait for pready
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{pready}.value) == 1:
                break

        # READ: same addr
        await RisingEdge(dut.clk)
        dut.{penable}.value = 0
        dut.{pwrite}.value = 0
        dut.{paddr}.value = addr
        await RisingEdge(dut.clk)
        dut.{penable}.value = 1
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{pready}.value) == 1:
                break

        # Idle cycle between transactions
        dut.{psel}.value = 0
        dut.{penable}.value = 0
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.{psel}.value = 0
    dut.{penable}.value = 0
    dut.{pwrite}.value = 0
'''

    monitor_py = '''
async def apb_monitor(dut, txns: list, coverpoint_sampler=None):
    """Record each completed APB transaction."""
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        # Sample on the cycle pready==1 with penable==1
        if (int(dut.penable.value) == 1 and int(dut.pready.value) == 1 and int(dut.psel.value) == 1):
            t = {
                "kind": "W" if int(dut.pwrite.value) == 1 else "R",
                "addr": int(dut.paddr.value),
                "wdata": int(dut.pwdata.value) if int(dut.pwrite.value) == 1 else None,
                "rdata": int(dut.prdata.value) if int(dut.pwrite.value) == 0 else None,
                "slverr": int(dut.pslverr.value) if "pslverr" in dir(dut) else 0,
            }
            txns.append(t)
            if coverpoint_sampler is not None:
                coverpoint_sampler("apb", t)
'''

    scoreboard_py = '''
class ApbScoreboard:
    """APB scoreboard: stores writes; reads must match."""
    def __init__(self, txns: list):
        self.txns = txns
        self.mem: dict = {}
        self.failures: list = []

    def check(self):
        for t in self.txns:
            if t["kind"] == "W":
                self.mem[t["addr"]] = t["wdata"]
            else:  # R
                if t["addr"] in self.mem and t["rdata"] != self.mem[t["addr"]]:
                    if len(self.failures) < 50:
                        self.failures.append(
                            f"APB_READ_MISMATCH@addr={t['addr']} expected={self.mem[t['addr']]} got={t['rdata']}"
                        )
                    return False
        return True
'''

    coverpoint_py = f'''
def _sample_apb_bins(dut, cp_registry):
    try:
        sel = int(dut.{psel}.value)
        en = int(dut.{penable}.value)
        wr = int(dut.{pwrite}.value)
        addr = int(dut.{paddr}.value)
    except Exception:
        return
    if sel != 1:
        return
    if "cp_pwrite" in cp_registry:
        cp = cp_registry["cp_pwrite"]
        if en == 1 and wr == 1:
            cp["BIN_WRITE"].hit += 1
        if en == 1 and wr == 0:
            cp["BIN_READ"].hit += 1
    if "cp_addr_align" in cp_registry:
        cp = cp_registry["cp_addr_align"]
        if en == 1:
            if (addr & 0x3) == 0:
                cp["BIN_ALIGNED"].hit += 1
            else:
                cp["BIN_MISALIGNED"].hit += 1
    if "cp_penable" in cp_registry:
        cp = cp_registry["cp_penable"]
        if sel == 1 and en == 0:
            cp["BIN_SETUP"].hit += 1
        if sel == 1 and en == 1:
            cp["BIN_ACCESS"].hit += 1
'''

    ports_handled = [p.name for p in design.ports
                     if p.protocol_group in {"apb", "clk", "rst"}]

    return ProtocolOutput(
        name="apb",
        handshake="two_phase",
        backpressure_strategy="wait_for_pready",
        driver_name="apb_driver",
        monitor_name="apb_monitor",
        scoreboard_name="ApbScoreboard",
        scoreboard_checks=["prdata_matches_stored_value", "pslverr_zero"],
        driver_py=driver_py,
        monitor_py=monitor_py,
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="apb",
    )
