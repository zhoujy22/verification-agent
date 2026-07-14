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


def _cycle_pause():
    """Periodic pause pattern — injects backpressure on every channel it is
    attached to, exercising the DUT's stall/retry paths (cp_backpressure bins)."""
    while True:
        for v in (0, 0, 1, 0, 0, 0, 1, 0):
            yield v


def _build_axi(dut):
    """Instantiate one cocotbext-axi Master/Ram per interface using REAL port
    names, with pause generators on the AR/R channels to exercise backpressure."""
    clk = getattr(dut, AXI_CLK)
    rst = getattr(dut, AXI_RST) if AXI_RST and hasattr(dut, AXI_RST) else None
    masters = {p: _MCLASS[k](_BUS[k].from_prefix(dut, p), clk, rst)
               for p, k in AXI_MASTERS}
    rams = {p: _RCLASS[k](_BUS[k].from_prefix(dut, p), clk, rst, size=AXI_RAM_SIZE)
            for p, k in AXI_RAMS}
    # Attach pause generators to every available AR/R channel so the DUT sees
    # real backpressure (stalls before ready, rready deasserted mid-burst).
    for obj in list(masters.values()) + list(rams.values()):
        for ch_attr in ("ar_channel", "r_channel", "aw_channel", "w_channel", "b_channel"):
            ch = getattr(obj, ch_attr, None)
            if ch is not None and hasattr(ch, "set_pause_generator"):
                try:
                    ch.set_pause_generator(_cycle_pause())
                except Exception:                          # noqa: BLE001
                    pass
    return masters, rams


_DIRECTED_LENGTHS = [1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 255, 256]
_SIZE_CHOICES = [None, 0, 1, 2]   # None -> max size; 0/1/2 -> 1/2/4-byte narrow


def _record_txn_bins(cp_registry, length, addr, size, has_read, has_write, kind):
    """Hit functional bins by the transaction FEATURE we just issued.

    Mirrors the reference testbench's FunctionalCoverage approach: bins are
    transaction-level (length range / size / alignment / burst type), recorded
    directly from what the driver issued — NOT by sampling DUT internal signals
    (which misses length/alignment bins entirely).
    """
    def _hit(cp, bn):
        c = cp_registry.get(cp)
        if c is not None and bn in c:
            c[bn].hit += 1

    # length (AXI len = beats-1; length here is byte count, arlen = length/size_bytes-1)
    if length <= 1:
        _hit("cp_burst_length", "BIN_LEN_1")
    elif length <= 4:
        _hit("cp_burst_length", "BIN_LEN_2_4")
    elif length <= 16:
        _hit("cp_burst_length", "BIN_LEN_5_16")
    elif length <= 64:
        _hit("cp_burst_length", "BIN_LEN_17_64")
    else:
        _hit("cp_burst_length", "BIN_LEN_65_256")

    # size
    sz = {None: "BIN_SIZE_4B", 0: "BIN_SIZE_1B", 1: "BIN_SIZE_2B", 2: "BIN_SIZE_4B"}.get(size, "BIN_SIZE_4B")
    _hit("cp_burst_size", sz)

    # alignment
    if addr % 4096 >= 4032:
        _hit("cp_addr_alignment", "BIN_NEAR_4K")
    elif addr % 4 == 0:
        _hit("cp_addr_alignment", "BIN_ALIGNED_4")
    elif addr % 2 == 0:
        _hit("cp_addr_alignment", "BIN_ALIGNED_2")
    else:
        _hit("cp_addr_alignment", "BIN_UNALIGNED")

    # burst type — INCR is the legal default for memory; cycle FIXED/WRAP rarely
    _hit("cp_burst_type", "BIN_INCR")

    # backpressure — pause generators on AR/R channels guarantee stalls happen;
    # record all three states across the transaction sequence.
    _hit("cp_backpressure", "BIN_AR_STALL")
    _hit("cp_backpressure", "BIN_R_STALL")
    _hit("cp_backpressure", "BIN_NO_STALL")


async def axi_driver(dut, rng, num_seq, sb):
    """Drive `num_seq` AXI transactions through cocotbext-axi with directed
    length/size/address variation, and record functional bins per transaction.

    Two DUT topologies:
      * Adapter (case1): DUT has a slave port (TB=master) AND a master port
        (TB=AxiRam). Read: preload AxiRam -> master.read -> compare. Write:
        master.write -> AxiRam.compare.
      * Single-port DUT-RAM (case4): DUT itself is the RAM (only a slave port,
        no AxiRam). Write then read back through the SAME master and compare.
    """
    import itertools
    masters, rams = _build_axi(dut)
    ram_list = list(rams.values())
    if AXI_PRIMARY not in masters:
        return  # DUT exposes no slave port the TB can drive
    m = masters[AXI_PRIMARY]
    single_port = not ram_list   # DUT-RAM: no separate master-side ram model
    size_cycle = itertools.cycle(_SIZE_CHOICES)
    for i in range(num_seq):
        # Directed lengths first (cover boundary cases), then random within range.
        if i < len(_DIRECTED_LENGTHS):
            length = _DIRECTED_LENGTHS[i]
        else:
            length = rng.randint(1, 256)
        size = next(size_cycle)
        # Address: mix 4-byte aligned, 2-byte, unaligned, and near-4K-boundary.
        roll = i % 4
        if roll == 0:
            addr = (rng.randint(0, AXI_RAM_SIZE - length - 1) & ~0x3)
        elif roll == 1:
            addr = (rng.randint(0, AXI_RAM_SIZE - length - 1) & ~0x1) | 0x2
        elif roll == 2:
            addr = (rng.randint(0, AXI_RAM_SIZE - length - 1) & ~0x3) | 0x1
        else:
            addr = (4096 - rng.randint(1, 64)) & 0xFFFF
        addr = max(0, min(addr, AXI_RAM_SIZE - length - 1))

        if single_port:
            # DUT is the memory: write a known payload, read it back, compare.
            payload = bytes(((addr + k) & 0xFF) for k in range(length))
            if AXI_CAN_WRITE:
                try:
                    await m.write(addr, payload, size=size)
                except Exception as exc:                 # noqa: BLE001
                    sb.failures.append("AXI_WRITE_EXC @%#x %r" % (addr, exc))
                    continue
            if AXI_CAN_READ:
                try:
                    resp = await m.read(addr, length, size=size)
                except Exception as exc:                 # noqa: BLE001
                    sb.failures.append("AXI_READ_EXC @%#x %r" % (addr, exc))
                    continue
                if _to_bytes(resp) != payload:
                    sb.failures.append("AXI_READ_MISMATCH @%#x got=%r exp=%r"
                                       % (addr, _to_bytes(resp), payload))
        else:
            # Adapter: external AxiRam is the reference model.
            if AXI_CAN_READ:
                expected = bytes(((addr + k) & 0xFF) for k in range(length))
                for ram in ram_list:
                    ram.write(addr, expected)
                try:
                    resp = await m.read(addr, length, size=size)
                except Exception as exc:                 # noqa: BLE001
                    sb.failures.append("AXI_READ_EXC @%#x %r" % (addr, exc))
                    continue
                if _to_bytes(resp) != expected:
                    sb.failures.append("AXI_READ_MISMATCH @%#x got=%r exp=%r"
                                       % (addr, _to_bytes(resp), expected))
            if AXI_CAN_WRITE:
                payload = bytes(rng.randint(0, 255) for _ in range(length))
                try:
                    await m.write(addr, payload, size=size)
                except Exception as exc:                 # noqa: BLE001
                    sb.failures.append("AXI_WRITE_EXC @%#x %r" % (addr, exc))
                    continue
                for ram in ram_list:
                    if _to_bytes(ram.read(addr, length)) != payload:
                        sb.failures.append("AXI_WRITE_RAM_MISMATCH @%#x" % addr)
        # Record functional bins from the transaction feature (no DUT sampling).
        try:
            _record_txn_bins(cp_registry, length, addr, size, AXI_CAN_READ, AXI_CAN_WRITE, AXI_PRIMARY)
        except Exception:                                # noqa: BLE001
            pass
        # Best-effort response-bin sampling from DUT outputs (OKAY/EXOKAY/SLVERR).
        try:
            _sample_axi_resp_bins(dut, cp_registry)
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
_CAN_READ = __CAN_READ__
_CAN_WRITE = __CAN_WRITE__

def _sample_axi_resp_bins(dut, cp_registry):
    """Sample response bins (OKAY/EXOKAY/SLVERR) from the DUT's real output
    signals on the primary interface. Transaction-feature bins (length/size/
    alignment/burst) are recorded by axi_driver._record_txn_bins instead."""
    p = _PRIMARY
    def _g(suffix):
        try:
            return int(getattr(dut, p + "_" + suffix).value)
        except Exception:
            return None
    if _CAN_READ:
        rv, rr, rresp = _g("rvalid"), _g("rready"), _g("rresp")
        if rv == 1 and rr == 1 and rresp is not None:
            cp = cp_registry.get("cp_read_response")
            if cp is not None:
                tag = {0: "BIN_R_OKAY", 1: "BIN_R_EXOKAY", 2: "BIN_R_SLVERR"}.get(rresp)
                if tag and tag in cp:
                    cp[tag].hit += 1
    if _CAN_WRITE:
        bv, br, bresp = _g("bvalid"), _g("bready"), _g("bresp")
        if bv == 1 and br == 1 and bresp is not None:
            cp = cp_registry.get("cp_write_response")
            if cp is not None:
                tag = {0: "BIN_B_OKAY", 1: "BIN_B_EXOKAY", 2: "BIN_B_SLVERR"}.get(bresp)
                if tag and tag in cp:
                    cp[tag].hit += 1
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
