"""Coverage collection package."""
from .line_branch import (
    parse_verilator_xml,
    parse_verilator_info,
    parse_verilator_dat,
    parse_icarus_dat,
)
from .functional import parse_cocotb_json, reconcile_with_bins
from .aggregator import compute as compute_combined

__all__ = [
    "parse_verilator_xml",
    "parse_verilator_info",
    "parse_verilator_dat",
    "parse_icarus_dat",
    "parse_cocotb_json",
    "reconcile_with_bins",
    "compute_combined",
]
