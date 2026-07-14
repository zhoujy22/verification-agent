"""Protocol-specific generator implementations.

Each `.py` exports a `generate(design)` -> ProtocolOutput callable.
"""
from .base import ProtocolOutput
from .axi_lite import generate as axi_lite
from .axi_full import generate as axi_full
from .axis import generate as axi_stream
from .sram import generate as sram
from .stream import generate as stream
from .apb import generate as apb
from .generic import generate as generic

PROTOCOL_REGISTRY = {
    "AXI4": axi_full,
    "AXI4-Lite": axi_lite,
    "AXI-Stream": axi_stream,
    "SRAM": sram,
    "valid_ready_stream": stream,
    "APB": apb,
    "": generic,           # ← Plan A: catch-all fallback (NOT empty)
    "passive": generic,    # also a fallback for unrecognized protocols
}


def for_design(design) -> ProtocolOutput:
    """Pick generator based on design.primary_protocol."""
    fn = PROTOCOL_REGISTRY.get(design.primary_protocol, generic)
    return fn(design)
