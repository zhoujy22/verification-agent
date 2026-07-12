"""Base class for protocol-specific generators."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ProtocolOutput:
    """Output of one protocol generator — strings of Python/cocotb source.

    `driver_name`, `monitor_name`, `scoreboard_name` MUST match the actual
    identifiers used in driver_py / monitor_py / scoreboard_py, so that
    verification_skeleton.json and the generated tb_top.py are aligned.
    """
    name: str                                   # protocol identifier, e.g. "axi_lite"
    handshake: str = "valid_ready"              # handshake flavor
    backpressure_strategy: str = "wait_for_ready"

    # Pieces of cocotb Python source — written to generated_tb/.
    driver_py: str = ""                         # coroutine that drives the DUT inputs
    monitor_py: str = ""                        # coroutine that samples DUT outputs
    scoreboard_py: str = ""                     # reference model / check code
    coverpoint_py: str = ""                     # cocotb-coverage sampling sites

    # Names used in driver_py / monitor_py / scoreboard_py — these MUST match the
    # actual definitions inside those source strings (e.g. "StreamScoreboard"
    # if class StreamScoreboard: ... is what scoreboard_py emits).
    driver_name: str = ""                       # Python identifier, e.g. "stream_driver"
    monitor_name: str = ""                      # e.g. "stream_monitor"
    scoreboard_name: str = ""                   # e.g. "StreamScoreboard"

    # Protocol-specific check names — written to verification_skeleton.json.
    scoreboard_checks: list[str] = field(default_factory=list)

    ports_handled: list[str] = field(default_factory=list)  # filled by concrete class
    protocol_group: str = ""                    # e.g. "axi_aw"


class ProtocolGenerator(Protocol):
    """Any protocol generator implements these methods."""
    name: str

    def generate(self) -> ProtocolOutput: ...
