"""Shared data classes for parsed RTL designs and protocol-annotated port groups.

These dataclasses back the `design.json` output and feed every downstream stage
(classifier, tb generator, coverage definer).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Direction = Literal["input", "output", "inout"]
Sign = Literal["unsigned", "signed"]
Role = Literal["clk", "rst", "driver", "monitor", "passive"]


@dataclass
class Port:
    """One Verilog port with protocol annotations applied by the classifier."""
    name: str
    direction: Direction
    width: int                              # 1 means scalar
    sign: Sign = "unsigned"
    protocol_group: str = "unknown"         # clk | rst | axi_aw | axi_w | axi_b | axi_ar | axi_r | sram_in | sram_out | stream_in | stream_out | passive
    role: Role = "passive"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Parameter:
    """Verilog `parameter` declaration."""
    name: str
    value: int
    width: int = 32
    signed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Clock:
    """Inferred clock domain."""
    name: str
    width: int = 1
    period_ns: int = 10

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Reset:
    """Inferred reset signal; active_level 0 means active-low."""
    name: str
    width: int = 1
    active_level: Literal[0, 1] = 0
    duration_cycles: int = 5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Design:
    """Fully-parsed RTL design with protocol annotations."""
    top: str
    rtl_files: list[str] = field(default_factory=list)
    compile_order: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    clock: list[Clock] = field(default_factory=list)
    reset: list[Reset] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    inferred_protocols: list[str] = field(default_factory=list)
    primary_protocol: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "case_name": self.top,
            "top": self.top,
            "rtl_files": list(self.rtl_files),
            "compile_order": list(self.compile_order),
            "include_dirs": list(self.include_dirs),
            "clock": [c.to_dict() for c in self.clock],
            "reset": [r.to_dict() for r in self.reset],
            "parameters": [p.to_dict() for p in self.parameters],
            "ports": [p.to_dict() for p in self.ports],
            "inferred_protocols": list(self.inferred_protocols),
            "primary_protocol": self.primary_protocol,
        }
