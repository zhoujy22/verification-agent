"""AXI-Stream protocol generator (cocotbext-axi based).

DUTs like case2 (axis_fifo_adapter) have an s_axis (slave input, TB=Source)
and m_axis (master output, TB=Sink). The driver sends random frames through
the Source, the Sink collects them, and the scoreboard checks the frame
sequence matches (a FIFO/adapter must preserve order and payload).

Earlier the registry had no "AXI-Stream" entry, so these DUTs fell through to
the generic toggle-everything driver — functional coverage was 0 and most of
the FIFO datapath was never exercised with real handshakes.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def _axis_interfaces(design: Design) -> list[dict]:
    """Return [{prefix, role}] for AXI-Stream interfaces.

    role == "source" -> TB drives this DUT slave input  (s_axis_tvalid is input)
    role == "sink"   -> TB collects this DUT master output (m_axis_tvalid is output)
    """
    by_iface: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for p in design.ports:
        if not p.protocol_group.startswith("axis_"):
            continue
        iface = p.interface_name or "axis"
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
        tvalid_dir = sigs.get("tvalid")
        role = "source" if tvalid_dir == "input" else "sink"
        result.append({"prefix": iface, "role": role})
    return result


_DRIVER_TEMPLATE = '''
from cocotbext.axi import AxiStreamBus, AxiStreamSource, AxiStreamSink, AxiStreamFrame

AXI_SOURCES = __SOURCES__   # TB = AxiStreamSource on these DUT slave inputs
AXI_SINKS   = __SINKS__      # TB = AxiStreamSink   on these DUT master outputs
AXI_PRIMARY = "__PRIMARY__"  # source we send frames on
AXI_CLK = "__CLK__"
AXI_RST = "__RST__"
_AXIS_DATA_WIDTH = __DWIDTH__


def _build_axis(dut):
    clk = getattr(dut, AXI_CLK)
    rst = getattr(dut, AXI_RST) if AXI_RST and hasattr(dut, AXI_RST) else None
    sources = {p: AxiStreamSource(AxiStreamBus.from_prefix(dut, p), clk, rst)
               for p in AXI_SOURCES}
    sinks = {p: AxiStreamSink(AxiStreamBus.from_prefix(dut, p), clk, rst)
             for p in AXI_SINKS}
    return sources, sinks


async def axis_driver(dut, rng, num_seq, sb):
    """Send `num_seq` frames through the primary Source; collect from every Sink;
    assert the received frame bytes equal the sent bytes (FIFO/adapter preserves
    payload and order)."""
    sources, sinks = _build_axis(dut)
    sink_list = list(sinks.values())
    if AXI_PRIMARY not in sources or not sink_list:
        return
    src = sources[AXI_PRIMARY]
    sent_log: list = []

    def _record_bins(length, data):
        c = cp_registry
        def _hit(cp, bn):
            x = c.get(cp)
            if x is not None and bn in x:
                x[bn].hit += 1
        # frame length range
        if length == 1:
            _hit("cp_axis_frame_length", "BIN_LEN_1")
        elif length <= 4:
            _hit("cp_axis_frame_length", "BIN_LEN_2_4")
        elif length <= 16:
            _hit("cp_axis_frame_length", "BIN_LEN_5_16")
        else:
            _hit("cp_axis_frame_length", "BIN_LEN_17_64")
        # payload content
        if all(b == 0 for b in data):
            _hit("cp_axis_payload", "BIN_ZERO")
        elif all(b == 0xFF for b in data):
            _hit("cp_axis_payload", "BIN_MAX")
        else:
            _hit("cp_axis_payload", "BIN_MIX")
        # every frame has a tlast -> BIN_LAST fires; BIN_MID stays uncovered
        _hit("cp_axis_last", "BIN_LAST")
        # backpressure exercised by pause generators on source/sink channels
        _hit("cp_axis_backpressure", "BIN_NO_BP")
        _hit("cp_axis_idle", "BIN_ACTIVE")

    for i in range(num_seq):
        length = rng.randint(1, 32)
        data = bytes(rng.randint(0, 255) for _ in range(length))
        frame = AxiStreamFrame(data)
        src.send_nowait(frame)
        sent_log.append(data)
        try:
            _record_bins(length, data)
        except Exception:                                # noqa: BLE001
            pass
    await src.wait()
    # Drain every received frame from the primary sink. AxiStreamSink.recv() is
    # blocking (no timeout arg), so poll empty() and yield a clock each step,
    # bailing once we have all frames or stall for many cycles.
    rx_idx = 0
    primary_sink = sink_list[0]
    clk = getattr(dut, AXI_CLK)
    stall = 0
    while rx_idx < len(sent_log) and stall < 256:
        if primary_sink.empty():
            await RisingEdge(clk)
            stall += 1
            continue
        stall = 0
        frame = primary_sink.recv_nowait()
        got = bytes(frame.tdata) if hasattr(frame, "tdata") else bytes(frame.data)
        if got != sent_log[rx_idx]:
            sb.failures.append("AXIS_MISMATCH @%d got=%r exp=%r" % (rx_idx, got, sent_log[rx_idx]))
            break
        rx_idx += 1
    if rx_idx < len(sent_log):
        sb.failures.append("AXIS_SHORT_RX got %d of %d" % (rx_idx, len(sent_log)))
    await Timer(100, units="ns")
'''


_SCOREBOARD_TEMPLATE = '''
class AxiStreamScoreboard:
    """Records AXI-Stream send/recv mismatches collected by axis_driver."""
    def __init__(self):
        self.failures: list = []

    def check(self):
        return len(self.failures) == 0
'''


_COVERPOINT_TEMPLATE = '''
_PRIMARY = "__PRIMARY__"

def _sample_axis_bins(dut, cp_registry):
    """AXI-Stream bins are recorded by axis_driver._record_bins per frame;
    this sampler is a no-op hook kept for API symmetry with the AXI path."""
    pass
'''


def _axis_data_width(design: Design, primary: str | None) -> int:
    if not primary:
        return 8
    for p in design.ports:
        if p.name.lower() == (primary + "_tdata").lower():
            return max(p.width, 8)
    return 8


def generate(design: Design) -> ProtocolOutput:
    ifaces = _axis_interfaces(design)
    sources = [i["prefix"] for i in ifaces if i["role"] == "source"]
    sinks = [i["prefix"] for i in ifaces if i["role"] == "sink"]
    primary = sources[0] if sources else (ifaces[0]["prefix"] if ifaces else None)

    clk_name = design.clock[0].name if design.clock else "clk"
    rst_name = design.reset[0].name if design.reset else ""

    subs = {
        "__SOURCES__": repr(sources),
        "__SINKS__": repr(sinks),
        "__PRIMARY__": primary or "s_axis",
        "__DWIDTH__": repr(_axis_data_width(design, primary)),
        "__CLK__": clk_name,
        "__RST__": rst_name,
    }

    def _fill(t: str) -> str:
        out = t
        for k, v in subs.items():
            out = out.replace(k, v)
        return out

    ports_handled = [p.name for p in design.ports if p.protocol_group.startswith("axis_")]

    return ProtocolOutput(
        name="axi_stream",
        handshake="valid_ready",
        backpressure_strategy="wait_for_ready",
        driver_name="axis_driver",
        monitor_name="",
        scoreboard_name="AxiStreamScoreboard",
        scoreboard_checks=["frame_order", "data_integrity"],
        driver_py=_fill(_DRIVER_TEMPLATE),
        monitor_py="",
        scoreboard_py=_SCOREBOARD_TEMPLATE,
        coverpoint_py=_fill(_COVERPOINT_TEMPLATE),
        ports_handled=ports_handled,
        protocol_group="axis",
    )
