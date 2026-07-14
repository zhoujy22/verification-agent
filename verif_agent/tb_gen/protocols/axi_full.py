"""Full AXI4 protocol generator.

cocotbext-axi's ``AxiBus.from_prefix`` auto-detects which channels
(AW/W/B/AR/R) are present on each interface, so the cocotbext-axi based driver
emitted by :mod:`axi_lite` handles AXI4 full, AXI4-Lite, and read-/write-only
adapters uniformly. This wrapper only marks the protocol as full and advertises
the burst-legal scoreboard check label.
"""
from __future__ import annotations

from .axi_lite import generate as _axi_generate
from .base import ProtocolOutput


def generate(design) -> ProtocolOutput:
    out = _axi_generate(design)
    out.name = "axi_full"
    out.scoreboard_checks = ["bresp_okay", "rresp_okay", "burst_legal"]
    return out
