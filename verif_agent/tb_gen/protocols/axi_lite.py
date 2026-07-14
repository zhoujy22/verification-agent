"""AXI4 / AXI4-Lite protocol generator (cocotbext-axi based).

Earlier versions emitted a hand-written driver that looked signals up by BARE
name (``if "awvalid" in ports else "AWValid"``). Real IPs prefix every signal
(``s_axi_awvalid``, ``m_axi_rdata``), so the lookup always fell through to
fantasy names (``AWValid``) that do not exist on the DUT — the generated
testbench crashed on the first ``dut.AWValid.value = 1`` with AttributeError
and coverage was 0%.

Fix: consume the classifier's per-port annotations (``interface_name`` /
``protocol_group`` / ``direction``) to resolve each AXI interface's real port
prefix, then emit a cocotb testbench built on cocotbext-axi:

  * ``AxiMaster`` on every slave-side interface  (DUT *valid is an input  -> TB drives)
  * ``AxiRam``    on every master-side interface (DUT *valid is an output -> TB serves)

cocotbext-axi's ``AxiBus.from_prefix(dut, "s_axi")`` does the prefix-tolerant
signal matching itself, so the generated driver references only real signals.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


# ---------------------------------------------------------------------------
# Resolve AXI interfaces from classifier annotations
# ---------------------------------------------------------------------------

def _axi_interfaces(design: Design) -> list[dict]:
    """Group AXI ports by interface prefix and tag each interface.

    Returns ``[{prefix, role, has_read, has_write}]`` (in declaration order),
    where:
      role == "master" -> TB drives this DUT slave port (DUT arvalid/awvalid is input)
      role == "ram"    -> TB backs this DUT master port (DUT arvalid/awvalid is output)
    """
    by_iface: dict[str, dict[str, str]] = {}   # prefix -> {bare_signal_lower: direction}
    order: list[str] = []
    for p in design.ports:
        if not p.protocol_group.startswith("axi_"):
            continue
        iface = p.interface_name or "axi"
        if iface not in by_iface:
            by_iface[iface] = {}
            order.append(iface)
        bare = p.name.lower()
        if iface and bare.startswith(iface.lower() + "_"):
            bare = bare[len(iface) + 1:]
        by_iface[iface][bare] = p.direction

    result: list[dict] = []
    for iface in order:
        sigs = by_iface[iface]
        has_read = "arvalid" in sigs and "rvalid" in sigs
        has_write = "awvalid" in sigs and "wvalid" in sigs and "bvalid" in sigs
        driver_sig_dir = sigs.get("arvalid") or sigs.get("awvalid") or sigs.get("avalid")
        role = "master" if driver_sig_dir == "input" else "ram"
        result.append({"prefix": iface, "role": role,
                       "has_read": has_read, "has_write": has_write})
    return result


# ---------------------------------------------------------------------------
# Source templates (placeholder-substituted; NO f-strings -> no brace escaping)
# ---------------------------------------------------------------------------

_DRIVER_TEMPLATE = '''
from cocotbext.axi import (AxiBus, AxiReadBus, AxiWriteBus,
                           AxiMaster, AxiMasterRead, AxiMasterWrite,
                           AxiRam, AxiRamRead, AxiRamWrite)

# (prefix, kind) per interface; kind in {"read","write","full"} selected from
# which AXI channels the classifier found, so a read-only adapter uses the
# read-only bus/master/ram classes and never touches a missing write channel.
AXI_MASTERS = __MASTERS__   # TB = AxiMaster* on these DUT slave ports
AXI_RAMS    = __RAMS__      # TB = AxiRam*    on these DUT master ports
AXI_PRIMARY = "__PRIMARY__"  # slave port the driver issues transactions on
AXI_CAN_READ  = __CAN_READ__
AXI_CAN_WRITE = __CAN_WRITE__
AXI_RAM_SIZE  = 1 << 16
AXI_CLK = "__CLK__"
AXI_RST = "__RST__"

_BUS    = {"read": AxiReadBus, "write": AxiWriteBus, "full": AxiBus}
_MCLASS = {"read": AxiMasterRead, "write": AxiMasterWrite, "full": AxiMaster}
_RCLASS = {"read": AxiRamRead, "write": AxiRamWrite, "full": AxiRam}


def _to_bytes(resp):
    if hasattr(resp, "data"):
        return bytes(resp.data)
    return bytes(resp)


def _build_axi(dut):
    """Instantiate one cocotbext-axi Master/Ram per interface using REAL port names."""
    clk = getattr(dut, AXI_CLK)
    rst = getattr(dut, AXI_RST) if AXI_RST and hasattr(dut, AXI_RST) else None
    masters = {p: _MCLASS[k](_BUS[k].from_prefix(dut, p), clk, rst)
               for p, k in AXI_MASTERS}
    rams = {p: _RCLASS[k](_BUS[k].from_prefix(dut, p), clk, rst, size=AXI_RAM_SIZE)
            for p, k in AXI_RAMS}
    return masters, rams


async def axi_driver(dut, rng, num_seq, sb):
    """Drive `num_seq` AXI transactions through cocotbext-axi.

    Read path: preload every AxiRam at `addr` with a known pattern, issue a
    read on the primary AxiMaster, assert the returned bytes match.
    Write path: issue a write on the primary AxiMaster, assert every AxiRam
    ends up holding the payload.
    """
    masters, rams = _build_axi(dut)
    ram_list = list(rams.values())
    if AXI_PRIMARY not in masters:
        return  # DUT exposes no slave port the TB can drive
    m = masters[AXI_PRIMARY]
    for i in range(num_seq):
        addr = rng.randint(0, AXI_RAM_SIZE - 64) & ~0x3
        length = rng.choice([1, 2, 4, 8])
        if AXI_CAN_READ:
            expected = bytes(((addr + k) & 0xFF) for k in range(length))
            for ram in ram_list:
                ram.write(addr, expected)
            try:
                resp = await m.read(addr, length)
            except Exception as exc:                     # noqa: BLE001
                sb.failures.append("AXI_READ_EXC @%#x %r" % (addr, exc))
                continue
            if _to_bytes(resp) != expected:
                sb.failures.append("AXI_READ_MISMATCH @%#x got=%r exp=%r"
                                   % (addr, _to_bytes(resp), expected))
        if AXI_CAN_WRITE:
            payload = bytes(rng.randint(0, 255) for _ in range(length))
            try:
                await m.write(addr, payload)
            except Exception as exc:                     # noqa: BLE001
                sb.failures.append("AXI_WRITE_EXC @%#x %r" % (addr, exc))
                continue
            for ram in ram_list:
                if _to_bytes(ram.read(addr, length)) != payload:
                    sb.failures.append("AXI_WRITE_RAM_MISMATCH @%#x" % addr)
        # Best-effort functional bin sampling; never fatal.
        try:
            _sample_axi_bins(dut, cp_registry)
        except Exception:                                # noqa: BLE001
            pass
    await Timer(100, units="ns")
'''


_SCOREBOARD_TEMPLATE = '''
class AxiScoreboard:
    """Records AxiMaster<->AxiRam read/write mismatches collected by axi_driver."""
    def __init__(self):
        self.failures: list = []

    def check(self):
        return len(self.failures) == 0
'''


_COVERPOINT_TEMPLATE = '''
_PRIMARY = "__PRIMARY__"

def _sample_axi_bins(dut, cp_registry):
    """Sample response bins on the primary interface using REAL signal names."""
    p = _PRIMARY
    try:
        rvalid = int(getattr(dut, p + "_rvalid").value)
        rready = int(getattr(dut, p + "_rready").value)
        rresp = int(getattr(dut, p + "_rresp").value)
    except Exception:                                    # noqa: BLE001
        return
    cp = cp_registry.get("cp_resp")
    if cp is not None and rvalid == 1 and rready == 1:
        if rresp == 0 and "BIN_OKAY" in cp:
            cp["BIN_OKAY"].hit += 1
        elif rresp == 1 and "BIN_EXOKAY" in cp:
            cp["BIN_EXOKAY"].hit += 1
        elif "BIN_OTHER" in cp:
            cp["BIN_OTHER"].hit += 1
    try:
        bvalid = int(getattr(dut, p + "_bvalid").value)
        bready = int(getattr(dut, p + "_bready").value)
        bresp = int(getattr(dut, p + "_bresp").value)
    except Exception:                                    # noqa: BLE001
        return
    if cp is not None and bvalid == 1 and bready == 1 and bresp == 0 and "BIN_BRESP_OK" in cp:
        cp["BIN_BRESP_OK"].hit += 1
'''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _kind(has_read: bool, has_write: bool) -> str:
    if has_read and has_write:
        return "full"
    if has_read:
        return "read"
    return "write"


def generate(design: Design) -> ProtocolOutput:
    ifaces = _axi_interfaces(design)
    masters = [i for i in ifaces if i["role"] == "master"]
    rams = [i for i in ifaces if i["role"] == "ram"]
    primary = masters[0] if masters else (ifaces[0] if ifaces else None)

    master_specs = [(i["prefix"], _kind(i["has_read"], i["has_write"])) for i in masters]
    ram_specs = [(i["prefix"], _kind(i["has_read"], i["has_write"])) for i in rams]
    can_read = bool(primary and primary["has_read"])
    can_write = bool(primary and primary["has_write"])
    primary_prefix = primary["prefix"] if primary else "axi"

    clk_name = design.clock[0].name if design.clock else "clk"
    rst_name = design.reset[0].name if design.reset else ""

    subs = {
        "__MASTERS__": repr(master_specs),
        "__RAMS__": repr(ram_specs),
        "__PRIMARY__": primary_prefix,
        "__CAN_READ__": repr(can_read),
        "__CAN_WRITE__": repr(can_write),
        "__CLK__": clk_name,
        "__RST__": rst_name,
    }

    def _fill(template: str) -> str:
        out = template
        for k, v in subs.items():
            out = out.replace(k, v)
        return out

    ports_handled = [p.name for p in design.ports if p.protocol_group.startswith("axi_")]

    return ProtocolOutput(
        name="axi_lite",
        handshake="valid_ready",
        backpressure_strategy="wait_for_ready",
        driver_name="axi_driver",
        monitor_name="",                 # cocotbext-axi monitors internally
        scoreboard_name="AxiScoreboard",
        scoreboard_checks=["bresp_okay", "rresp_okay", "burst_legal"],
        driver_py=_fill(_DRIVER_TEMPLATE),
        monitor_py="",
        scoreboard_py=_SCOREBOARD_TEMPLATE,
        coverpoint_py=_fill(_COVERPOINT_TEMPLATE),
        ports_handled=ports_handled,
        protocol_group="axi",
    )
