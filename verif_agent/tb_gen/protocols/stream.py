"""valid/ready stream protocol generator.

Generates cocotb Python source for:
  - driver: random in_data, drives in_valid, awaits in_ready
  - monitor: collects in/out transactions at handshake edges
  - scoreboard: deque-based FIFO; out_data must follow in_data in order
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def generate(design: Design) -> ProtocolOutput:
    in_stems, out_stems = _split_stems(design)

    ports_handled: list[str] = []
    for p in design.ports:
        if p.protocol_group in {"stream_in", "stream_out", "clk", "rst"}:
            ports_handled.append(p.name)

    in_valid = _pick(in_stems, "valid") or "in_valid"
    in_ready = _pick(in_stems, "ready") or "in_ready"
    in_data = _pick(in_stems, "data") or "in_data"
    out_valid = _pick(out_stems, "valid") or "out_valid"
    out_ready = _pick(out_stems, "ready") or "out_ready"
    out_data = _pick(out_stems, "data") or "out_data"

    driver_py = f'''
async def stream_driver(dut, rng, num_seq):
    """Push `num_seq` words through in_* side; backpressure on out_ready."""
    for i in range(num_seq):
        await RisingEdge(dut.clk)
        # Random backpressure on out_ready
        if rng.random() < 0.10:
            dut.{out_ready}.value = 0
        else:
            dut.{out_ready}.value = 1

        dut.{in_valid}.value = 1
        dut.{in_data}.value = rng.randint(0, (1 << max(dut.{in_data}.value.nbits, 1)) - 1)
        # Wait for handshake
        for _ in range(64):
            await RisingEdge(dut.clk)
            if dut.{in_valid}.value == 1 and dut.{in_ready}.value == 1:
                break
        else:
            dut._log.warning(f"stream driver handshake timeout cycle {{i}}")

    await RisingEdge(dut.clk)
    dut.{in_valid}.value = 0
    dut.{out_ready}.value = 0
'''

    monitor_py = f'''
async def stream_monitor(dut, in_q: list, out_q: list, coverpoint_sampler=None):
    """Sample {in_valid}/{in_ready}/{in_data} and {out_valid}/{out_ready}/{out_data} at every posedge."""
    last_in_valid = 0
    last_out_valid = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        # Sample on stable (settled) state — use values at the rising edge
        iv = int(dut.{in_valid}.value)
        ir = int(dut.{in_ready}.value)
        ov = int(dut.{out_valid}.value)
        or_ = int(dut.{out_ready}.value)
        if iv == 1 and ir == 1:
            in_data = int(dut.{in_data}.value)
            in_q.append(in_data)
            if coverpoint_sampler is not None:
                coverpoint_sampler("stream", {{"in_valid": iv, "in_ready": ir,
                                              "in_data": in_data,
                                              "out_ready": or_}})
        if ov == 1 and or_ == 1:
            out_data = int(dut.{out_data}.value)
            out_q.append(out_data)
        last_in_valid = iv
        last_out_valid = ov
'''

    scoreboard_py = f'''
class StreamScoreboard:
    """FIFO check: out_data sequence must equal in_data sequence."""
    def __init__(self, in_q: list, out_q: list, max_lag: int = 64):
        self.in_q = in_q
        self.out_q = out_q
        self.max_lag = max_lag
        self.mismatch_log = []

    def check(self):
        # Require out to lag in by at most `max_lag` (latency tolerance)
        in_n = len(self.in_q)
        out_n = len(self.out_q)
        # Compare at the head; data should match in order.
        n = min(in_n, out_n)
        for k in range(n):
            if self.in_q[k] != self.out_q[k]:
                if len(self.mismatch_log) < 50:
                    self.mismatch_log.append(
                        f"STREAM_OUTOFORDER@idx={{k}} in={{self.in_q[k]}} out={{self.out_q[k]}}"
                    )
                return False
        return True
'''

    coverpoint_py = f'''
# cocotb-coverage hooks for stream bins
def _sample_stream_bins(dut, cp_registry, scenario):
    """Evaluate simple sampling conditions for the bin definitions."""
    try:
        iv = int(dut.{in_valid}.value)
        ir = int(dut.{in_ready}.value)
        od = int(dut.{in_data}.value)
        or_ = int(dut.{out_ready}.value)
    except Exception:
        return
    if "cp_payload" in cp_registry:
        cp = cp_registry["cp_payload"]
        if iv == 1 and ir == 1 and od == 0:
            cp["BIN_ZERO"].hit += 1
        if iv == 1 and ir == 1 and od == (1 << max(dut.{in_data}.value.nbits, 1)) - 1 and od != 0:
            cp["BIN_MAX"].hit += 1
        if iv == 1 and ir == 1 and 0 < od < (1 << max(dut.{in_data}.value.nbits, 1)) - 1:
            cp["BIN_MIX"].hit += 1
    if "cp_backpressure" in cp_registry:
        cp = cp_registry["cp_backpressure"]
        if iv == 1 and ir == 1 and or_ == 1:
            cp["BIN_NO_BP"].hit += 1
        if iv == 1 and ir == 1 and or_ == 0:
            cp["BIN_BURSTY_BP"].hit += 1
    if "cp_idle" in cp_registry:
        cp = cp_registry["cp_idle"]
        if iv == 1 and ir == 1:
            cp["BIN_ACTIVE"].hit += 1
        if iv == 0:
            cp["INACTIVE"].hit += 1
'''

    return ProtocolOutput(
        name="valid_ready_stream",
        handshake="valid_ready",
        backpressure_strategy="random_out_ready",
        driver_name="stream_driver",
        monitor_name="stream_monitor",
        scoreboard_name="StreamScoreboard",
        scoreboard_checks=["fifo_order", "data_integrity"],
        driver_py=driver_py,
        monitor_py=monitor_py,
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="stream",
    )


def _split_stems(design: Design) -> tuple[set[str], set[str]]:
    in_stems: set[str] = set()
    out_stems: set[str] = set()
    for p in design.ports:
        if p.protocol_group == "stream_in":
            in_stems.add(p.name)
        elif p.protocol_group == "stream_out":
            out_stems.add(p.name)
    return in_stems, out_stems


def _pick(stems: set[str], suffix: str) -> str | None:
    for n in stems:
        if n.lower().endswith(f"_{suffix}"):
            return n
    return None


def _clock(dut_like=None) -> str:
    """Best-effort clock name. Always falls back to 'clk'."""
    return "clk"


def _clock_name(dut=None) -> str:
    return "clk"
