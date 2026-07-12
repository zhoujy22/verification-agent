"""AXI4-Lite protocol generator.

Lightweight transaction-level driver/monitor/scoreboard for AXI4-Lite.
Burst/length signals are absent, so each transaction is one beat.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def generate(design: Design) -> ProtocolOutput:
    ports = {p.name: p for p in design.ports}
    awaddr = "awaddr" if "awaddr" in ports else "AWAddr"
    awvalid = "awvalid" if "awvalid" in ports else "AWValid"
    awready = "awready" if "awready" in ports else "AWReady"
    wdata = "wdata" if "wdata" in ports else "WData"
    wvalid = "wvalid" if "wvalid" in ports else "WValid"
    wready = "wready" if "wready" in ports else "WReady"
    wstrb = "wstrb" if "wstrb" in ports else "WStrb"
    bvalid = "bvalid" if "bvalid" in ports else "BValid"
    bready = "bready" if "bready" in ports else "BReady"
    bresp = "bresp" if "bresp" in ports else "BResp"
    araddr = "araddr" if "araddr" in ports else "ARAddr"
    arvalid = "arvalid" if "arvalid" in ports else "ARValid"
    arready = "arready" if "arready" in ports else "ARReady"
    rdata = "rdata" if "rdata" in ports else "RData"
    rresp = "rresp" if "rresp" in ports else "RResp"
    rvalid = "rvalid" if "rvalid" in ports else "RValid"
    rready = "rready" if "rready" in ports else "RReady"

    driver_py = f'''
async def axi_lite_driver(dut, rng, num_seq):
    """Drive `num_seq` write+read transactions on AXI4-Lite."""
    for i in range(num_seq):
        # WRITE: aw+w
        await RisingEdge(dut.clk)
        addr = (rng.randint(0, (1 << 32) - 1)) & ~0x3
        data = rng.randint(0, (1 << 32) - 1)
        strobe = rng.randint(1, 0xF)
        dut.{awvalid}.value = 1
        dut.{awaddr}.value = addr
        dut.{wvalid}.value = 1
        dut.{wdata}.value = data
        dut.{wstrb}.value = strobe
        # Wait for handshakes
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{awready}.value) == 1:
                dut.{awvalid}.value = 0
                break
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{wready}.value) == 1:
                dut.{wvalid}.value = 0
                break
        # B response
        dut.{bready}.value = 1
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{bvalid}.value) == 1:
                break
        dut.{bready}.value = 0

        # READ: ar -> r
        await RisingEdge(dut.clk)
        raddr = (rng.randint(0, (1 << 32) - 1)) & ~0x3
        dut.{arvalid}.value = 1
        dut.{araddr}.value = raddr
        dut.{rready}.value = 1
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{arready}.value) == 1:
                dut.{arvalid}.value = 0
                break
        for _ in range(64):
            await RisingEdge(dut.clk)
            if int(dut.{rvalid}.value) == 1:
                dut.{rready}.value = 0
                break
        if int(dut.{arvalid}.value) == 1:
            dut.{arvalid}.value = 0
        if int(dut.{rready}.value) == 1:
            dut.{rready}.value = 0
    await RisingEdge(dut.clk)
    for s in ("{awvalid}", "{wvalid}", "{bready}", "{arvalid}", "{rready}"):
        try:
            getattr(dut, s).value = 0
        except Exception:
            pass
'''

    monitor_py = '''
async def axi_lite_monitor(dut, txns: list, coverpoint_sampler=None):
    """Record each completed transaction dict."""
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.bvalid.value) == 1 and int(dut.bready.value) == 1:
            txns.append({"kind": "B", "bresp": int(dut.bresp.value)})
        if int(dut.rvalid.value) == 1 and int(dut.rready.value) == 1:
            txns.append({"kind": "R", "rdata": int(dut.rdata.value),
                         "rresp": int(dut.rresp.value)})
'''

    scoreboard_py = '''
class AxiLiteScoreboard:
    """Checks: every B resp == 0 (OKAY), every R resp == 0 (OKAY).
    Records failures for the report.
    """
    def __init__(self, txns: list):
        self.txns = txns
        self.failures = []

    def check(self):
        for t in self.txns:
            if t["kind"] == "B" and t["bresp"] != 0:
                if len(self.failures) < 50:
                    self.failures.append(f"AXI_BRESP_NOT_OKAY: {{t}}")
                return False
            if t["kind"] == "R" and t["rresp"] != 0:
                if len(self.failures) < 50:
                    self.failures.append(f"AXI_RRESP_NOT_OKAY: {{t}}")
                return False
        return True
'''

    coverpoint_py = f'''
def _sample_axi_bins(dut, cp_registry):
    try:
        av = int(dut.{awvalid}.value); ar = int(dut.{awready}.value)
        aa = int(dut.{awaddr}.value)
        ws = int(dut.{wstrb}.value); wv = int(dut.{wvalid}.value); wr = int(dut.{wready}.value)
        bv = int(dut.{bvalid}.value); br = int(dut.{bready}.value); bresp = int(dut.{bresp}.value)
        rv = int(dut.{rvalid}.value); rresp = int(dut.{rresp}.value)
    except Exception:
        return
    if "cp_wstrb_pattern" in cp_registry:
        cp = cp_registry["cp_wstrb_pattern"]
        if wv == 1 and wr == 1 and ws == 0xF:
            cp["BIN_FULL"].hit += 1
        if wv == 1 and wr == 1 and 0 < ws < 0xF:
            cp["BIN_PARTIAL"].hit += 1
        if wv == 1 and wr == 1 and ws == 0:
            cp["BIN_EMPTY"].hit += 1
    if "cp_resp" in cp_registry:
        cp = cp_registry["cp_resp"]
        if rv == 1 and rresp == 0:
            try:
                if int(dut.rready.value) == 1:
                    cp["BIN_OKAY"].hit += 1
            except Exception:
                pass
        if rv == 1 and rresp == 1:
            try:
                if int(dut.rready.value) == 1:
                    cp["BIN_EXOKAY"].hit += 1
            except Exception:
                pass
        if bv == 1 and bresp == 0:
            try:
                if int(dut.bready.value) == 1:
                    cp["BIN_BRESP_OK"].hit += 1
            except Exception:
                pass
'''

    ports_handled = [p.name for p in design.ports
                     if p.protocol_group.startswith("axi") or p.protocol_group in {"clk", "rst"}]

    return ProtocolOutput(
        name="axi_lite",
        handshake="valid_ready",
        backpressure_strategy="wait_for_ready",
        driver_name="axi_lite_driver",
        monitor_name="axi_lite_monitor",
        scoreboard_name="AxiLiteScoreboard",
        scoreboard_checks=["bresp_okay", "rresp_okay"],
        driver_py=driver_py,
        monitor_py=monitor_py,
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="axi_aw",
    )
