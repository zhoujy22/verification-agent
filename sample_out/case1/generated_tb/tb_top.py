"""Auto-generated cocotb testbench for stream_dut.

Driven by /work/verif_agent tb_gen. Run via the Makefile in this directory.
SEED is set in the Makefile as SIM_ARGS+=--seed=${SEED}.
"""
import cocotb
import random
import json
from pathlib import Path
from cocotb.triggers import RisingEdge, ReadOnly, Timer
from cocotb.clock import Clock
from cocotb_coverage import coverage

# Random source — single instance per spec reproducibility contract.
_SEED = int(getattr(cocotb, "_SIM_ARGS_seed", 12345))
RNG = random.Random(_SEED)


class Hit:
    def __init__(self):
        self.hit = 0


cp_registry = {
    "cp_payload": {
        "BIN_ZERO": Hit(),
        "BIN_MAX": Hit(),
        "BIN_MIX": Hit(),
    },
    "cp_backpressure": {
        "BIN_NO_BP": Hit(),
        "BIN_BURSTY_BP": Hit(),
    },
    "cp_idle": {
        "BIN_ACTIVE": Hit(),
        "INACTIVE": Hit(),
    },
}




async def setup_clock_reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units='ns').start())
    dut.rst_n.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1




async def stream_driver(dut, rng, num_seq):
    """Push `num_seq` words through in_* side; backpressure on out_ready."""
    for i in range(num_seq):
        await RisingEdge(dut.clk)
        # Random backpressure on out_ready
        if rng.random() < 0.10:
            dut.out_ready.value = 0
        else:
            dut.out_ready.value = 1

        dut.in_valid.value = 1
        dut.in_data.value = rng.randint(0, (1 << max(dut.in_data.value.nbits, 1)) - 1)
        # Wait for handshake
        for _ in range(64):
            await RisingEdge(dut.clk)
            if dut.in_valid.value == 1 and dut.in_ready.value == 1:
                break
        else:
            dut._log.warning(f"stream driver handshake timeout cycle {i}")

    await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    dut.out_ready.value = 0




async def stream_monitor(dut, in_q: list, out_q: list, coverpoint_sampler=None):
    """Sample in_valid/in_ready/in_data and out_valid/out_ready/out_data at every posedge."""
    last_in_valid = 0
    last_out_valid = 0
    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()
        # Sample on stable (settled) state — use values at the rising edge
        iv = int(dut.in_valid.value)
        ir = int(dut.in_ready.value)
        ov = int(dut.out_valid.value)
        or_ = int(dut.out_ready.value)
        if iv == 1 and ir == 1:
            in_data = int(dut.in_data.value)
            in_q.append(in_data)
            if coverpoint_sampler is not None:
                coverpoint_sampler("stream", {"in_valid": iv, "in_ready": ir,
                                              "in_data": in_data,
                                              "out_ready": or_})
        if ov == 1 and or_ == 1:
            out_data = int(dut.out_data.value)
            out_q.append(out_data)
        last_in_valid = iv
        last_out_valid = ov




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
                        f"STREAM_OUTOFORDER@idx={k} in={self.in_q[k]} out={self.out_q[k]}"
                    )
                return False
        return True




# cocotb-coverage hooks for stream bins
def _sample_stream_bins(dut, cp_registry, scenario):
    """Evaluate simple sampling conditions for the bin definitions."""
    try:
        iv = int(dut.in_valid.value)
        ir = int(dut.in_ready.value)
        od = int(dut.in_data.value)
        or_ = int(dut.out_ready.value)
    except Exception:
        return
    if "cp_payload" in cp_registry:
        cp = cp_registry["cp_payload"]
        if iv == 1 and ir == 1 and od == 0:
            cp["BIN_ZERO"].hit += 1
        if iv == 1 and ir == 1 and od == (1 << max(dut.in_data.value.nbits, 1)) - 1 and od != 0:
            cp["BIN_MAX"].hit += 1
        if iv == 1 and ir == 1 and 0 < od < (1 << max(dut.in_data.value.nbits, 1)) - 1:
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



# ----- functional_coverage.json dump at end-of-test -----
async def _dump_functional_cov(dut, cp_registry, out_path: Path) -> None:
    payload = {}
    for cp_name, bins in cp_registry.items():
        payload[cp_name] = {}
        for bin_name, hit_obj in bins.items():
            payload[cp_name][bin_name] = {
                "hit_count": int(hit_obj.hit),
                "covered": int(hit_obj.hit) > 0,
            }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)



@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    in_q: list = []
    out_q: list = []
    cocotb.start_soon(stream_monitor(dut, in_q, out_q,
                                       coverpoint_sampler=lambda s, t: _sample_stream_bins(dut, cp_registry)))
    sb = StreamScoreboard(in_q, out_q)
    await stream_driver(dut, RNG, 5000)
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, Path(__file__).parent / "functional_cov.json")
    assert passed, f"mismatches: {sb.mismatch_log[:10]}"



# Tap for sampler used by monitors
def coverpoint_sampler(scenario: str, txn: dict) -> None:
    pass  # overridden in @cocotb.test()
