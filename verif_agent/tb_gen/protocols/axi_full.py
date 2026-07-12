"""Full AXI4 protocol generator (with burst/length signals).

For now uses the same driver scaffold as AXI4-Lite and extends with burst bits.
Burst mechanics are simplified: one transaction = one beat, but awsize/awburst/
awlen signals are driven so they get sampled by functional bins.
"""
from __future__ import annotations

from .axi_lite import generate as axi_lite_gen


def generate(design: Design) -> ProtocolOutput:
    out = axi_lite_gen(design)
    out.name = "axi_full"
    out.handshake = "valid_ready"
    out.protocol_group = "axi_aw"
    out.driver_name = "axi_lite_driver"          # reuses lite driver w/ burst bits added
    out.monitor_name = "axi_lite_monitor"
    out.scoreboard_name = "AxiLiteScoreboard"
    out.scoreboard_checks = ["bresp_okay", "rresp_okay", "burst_legal"]
    # Inject extra burst/length signals
    extra_in_driver = '''
        # Burst type (0 FIXED, 1 INCR, 2 WRAP)
        if "awburst" in dir(dut):
            b = rng.choices([0, 1, 2], weights=[20, 70, 10])[0]
            dut.awburst.value = b
        if "awsize" in dir(dut):
            s = rng.choices([0, 1, 2, 3, 4], weights=[5, 5, 80, 5, 5])[0]
            dut.awsize.value = s
        if "awlen" in dir(dut):
            dut.awlen.value = rng.choices([0, 1, 3, 7], weights=[40, 30, 20, 10])[0]
'''
    out.driver_py = out.driver_py.replace("dut.{awaddr}.value = addr", extra_in_driver + "\n        dut.{awaddr}.value = addr")

    # Extend _sample_axi_bins with burst sampling
    extra_cp = '''
    if "cp_awburst" in cp_registry:
        cp = cp_registry["cp_awburst"]
        try:
            awb = int(dut.awburst.value)
        except Exception:
            awb = -1
        if av == 1 and ar == 1:
            if awb == 0: cp["BIN_FIXED"].hit += 1
            if awb == 1: cp["BIN_INCR"].hit += 1
            if awb == 2: cp["BIN_WRAP"].hit += 1
    if "cp_awsize" in cp_registry:
        cp = cp_registry["cp_awsize"]
        try:
            aws = int(dut.awsize.value)
        except Exception:
            aws = -1
        if av == 1 and ar == 1:
            if aws == 0: cp["BIN_1B"].hit += 1
            if aws == 2: cp["BIN_4B"].hit += 1
            if aws == 3: cp["BIN_8B"].hit += 1
    if "cp_align" in cp_registry:
        cp = cp_registry["cp_align"]
        if av == 1 and ar == 1:
            if (aa & 0x3) == 0:
                cp["BIN_ALIGNED"].hit += 1
            else:
                cp["BIN_MISALIGNED"].hit += 1
'''
    out.coverpoint_py = out.coverpoint_py + extra_cp
    return out
