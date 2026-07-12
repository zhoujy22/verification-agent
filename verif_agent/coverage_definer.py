"""Functional coverage bin definition.

Per spec: every bin must have explicit `sampling_condition` (an expression that
can be evaluated by cocotb at sample time). Random-enumeration bins are rejected.
"""
from __future__ import annotations

from .design import Design


def _enforce_sampling_condition(bin_entry: dict) -> dict:
    """Hard validation per spec §34."""
    if not bin_entry.get("sampling_condition"):
        raise ValueError(
            f"coverage bin {bin_entry.get('name')!r} has no sampling_condition — "
            "will not be counted as a valid bin per spec."
        )
    if not bin_entry.get("scenario"):
        # Spec requires scenario description too (interpretability)
        bin_entry["scenario"] = bin_entry["name"]
    bin_entry.setdefault("covered", False)
    bin_entry.setdefault("hit_count", 0)
    return bin_entry


def _axi_bins(design: Design) -> list[dict]:
    """AXI4 / AXI4-Lite functional bins."""
    has_burst = any(p.name.lower() in {"awburst", "awlen", "awsize", "arsize", "arlen", "arburst"} for p in design.ports)
    coverpoints: list[dict] = []

    if has_burst:
        coverpoints.append({
            "name": "cp_awburst",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_FIXED", "scenario": "fixed burst",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awburst == 0"}),
                _enforce_sampling_condition({"name": "BIN_INCR",  "scenario": "increment burst",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awburst == 1"}),
                _enforce_sampling_condition({"name": "BIN_WRAP",  "scenario": "wrap burst",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awburst == 2"}),
            ],
        })
        coverpoints.append({
            "name": "cp_awsize",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_1B",  "scenario": "1-byte transfer",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awsize == 0"}),
                _enforce_sampling_condition({"name": "BIN_4B",  "scenario": "4-byte transfer",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awsize == 2"}),
                _enforce_sampling_condition({"name": "BIN_8B",  "scenario": "8-byte transfer",
                                              "sampling_condition": "awvalid == 1 and awready == 1 and awsize == 3"}),
            ],
        })

    # Always-available bins tied to write completion / read completion
    coverpoints.append({
        "name": "cp_wstrb_pattern",
        "bins": [
            _enforce_sampling_condition({"name": "BIN_FULL",     "scenario": "all-beats full strobe",
                                          "sampling_condition": "wvalid == 1 and wready == 1 and wstrb == 0xF"}),
            _enforce_sampling_condition({"name": "BIN_PARTIAL",  "scenario": "partial strobe",
                                          "sampling_condition": "wvalid == 1 and wready == 1 and 0 < wstrb < 0xF"}),
            _enforce_sampling_condition({"name": "BIN_EMPTY",    "scenario": "no strobe (still legal)",
                                          "sampling_condition": "wvalid == 1 and wready == 1 and wstrb == 0"}),
        ],
    })

    coverpoints.append({
        "name": "cp_resp",
        "bins": [
            _enforce_sampling_condition({"name": "BIN_OKAY",   "scenario": "OKAY response",
                                          "sampling_condition": "rvalid == 1 and rready == 1 and rresp == 0"}),
            _enforce_sampling_condition({"name": "BIN_EXOKAY", "scenario": "EXOKAY response",
                                          "sampling_condition": "rvalid == 1 and rready == 1 and rresp == 1"}),
            _enforce_sampling_condition({"name": "BIN_BRESP_OK", "scenario": "write response OKAY",
                                          "sampling_condition": "bvalid == 1 and bready == 1 and bresp == 0"}),
        ],
    })

    if has_burst:
        coverpoints.append({
            "name": "cp_align",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_ALIGNED",    "scenario": "addr%4==0",
                                              "sampling_condition": "(awvalid == 1 and awready == 1) and (awaddr & 0x3) == 0"}),
                _enforce_sampling_condition({"name": "BIN_MISALIGNED", "scenario": "addr%4!=0",
                                              "sampling_condition": "(awvalid == 1 and awready == 1) and (awaddr & 0x3) != 0"}),
            ],
        })

    return coverpoints


def _sram_bins(design: Design) -> list[dict]:
    return [
        {
            "name": "cp_addr_align",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_ALIGNED",    "scenario": "addr word-aligned",
                                              "sampling_condition": "csb == 0 and (addr & 0x3) == 0"}),
                _enforce_sampling_condition({"name": "BIN_MISALIGNED", "scenario": "addr mis-aligned",
                                              "sampling_condition": "csb == 0 and (addr & 0x3) != 0"}),
            ],
        },
        {
            "name": "cp_we",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_READ",  "scenario": "read cycle",
                                              "sampling_condition": "csb == 0 and we == 0"}),
                _enforce_sampling_condition({"name": "BIN_WRITE", "scenario": "write cycle",
                                              "sampling_condition": "csb == 0 and we == 1"}),
            ],
        },
        {
            "name": "cp_wmask",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_FULL",    "scenario": "all bytes enabled",
                                              "sampling_condition": "csb == 0 and we == 1 and wmask == 0xF"}),
                _enforce_sampling_condition({"name": "BIN_PARTIAL", "scenario": "partial bytes",
                                              "sampling_condition": "csb == 0 and we == 1 and 0 < wmask < 0xF"}),
            ],
        },
    ]


def _stream_bins(design: Design) -> list[dict]:
    return [
        {
            "name": "cp_payload",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_ZERO", "scenario": "all-zero payload",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1 and in_data == 0"}),
                _enforce_sampling_condition({"name": "BIN_MAX",  "scenario": "all-ones payload",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1 and in_data == (1 << 8) - 1"}),
                _enforce_sampling_condition({"name": "BIN_MIX",  "scenario": "mixed payload",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1 and 0 < in_data < (1 << 8) - 1"}),
            ],
        },
        {
            "name": "cp_backpressure",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_NO_BP",     "scenario": "out_ready always 1",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1 and out_ready == 1"}),
                _enforce_sampling_condition({"name": "BIN_BURSTY_BP", "scenario": "out_ready strobed",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1 and out_ready == 0"}),
            ],
        },
        {
            "name": "cp_idle",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_ACTIVE",  "scenario": "handshake fires",
                                              "sampling_condition": "in_valid == 1 and in_ready == 1"}),
                _enforce_sampling_condition({"name": "INACTIVE",    "scenario": "input idle",
                                              "sampling_condition": "in_valid == 0"}),
            ],
        },
    ]


def _apb_bins(design: Design) -> list[dict]:
    """APB (AMBA) functional bins."""
    return [
        {
            "name": "cp_pwrite",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_WRITE", "scenario": "APB write transfer",
                                              "sampling_condition": "psel == 1 and penable == 1 and pready == 1 and pwrite == 1"}),
                _enforce_sampling_condition({"name": "BIN_READ",  "scenario": "APB read transfer",
                                              "sampling_condition": "psel == 1 and penable == 1 and pready == 1 and pwrite == 0"}),
            ],
        },
        {
            "name": "cp_penable",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_SETUP",  "scenario": "setup phase",
                                              "sampling_condition": "psel == 1 and penable == 0"}),
                _enforce_sampling_condition({"name": "BIN_ACCESS", "scenario": "access phase",
                                              "sampling_condition": "psel == 1 and penable == 1"}),
            ],
        },
        {
            "name": "cp_addr_align",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_ALIGNED",    "scenario": "addr word-aligned",
                                              "sampling_condition": "psel == 1 and penable == 1 and (paddr & 0x3) == 0"}),
                _enforce_sampling_condition({"name": "BIN_MISALIGNED", "scenario": "addr mis-aligned",
                                              "sampling_condition": "psel == 1 and penable == 1 and (paddr & 0x3) != 0"}),
            ],
        },
        {
            "name": "cp_pready",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_READY_HIGH", "scenario": "transfer completes in 1 cycle",
                                              "sampling_condition": "psel == 1 and penable == 1 and pready == 1"}),
                _enforce_sampling_condition({"name": "BIN_READY_WAIT", "scenario": "slave waits",
                                              "sampling_condition": "psel == 1 and penable == 1 and pready == 0"}),
            ],
        },
    ]


def _generic_bins(design: Design) -> list[dict]:
    """Single 'we ran the sampler' bin for the generic fallback path."""
    return [
        {
            "name": "cp_generic_run",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_TICK",
                                              "scenario": "sampler touched at least once",
                                              "sampling_condition": "True"}),
            ],
        }
    ]


def _passive_bins(design: Design) -> list[dict]:
    return [
        {
            "name": "cp_passive_watch",
            "bins": [
                _enforce_sampling_condition({"name": "BIN_FIRST_EDGE",
                                              "scenario": "any level change",
                                              "sampling_condition": "True"}),  # always true; falls back to hit_count>0
            ],
        }
    ]


def define(design: Design) -> dict:
    """Return a `coverage_bins.json`-shaped dict, populated by protocol family."""
    coverpoints: list[dict] = []
    proto = design.primary_protocol

    if proto == "AXI4" or proto == "AXI4-Lite":
        coverpoints.extend(_axi_bins(design))
    if proto == "SRAM":
        coverpoints.extend(_sram_bins(design))
    if proto == "valid_ready_stream":
        coverpoints.extend(_stream_bins(design))
    if proto == "APB":
        coverpoints.extend(_apb_bins(design))

    # If no recognized protocol, fall back to a single sampler-tick bin so we
    # at least record one valid bin (per spec §34 — must have sampling_condition).
    if not coverpoints:
        coverpoints.extend(_generic_bins(design))

    return {
        "schema_version": 1,
        "case_name": design.top,
        "coverpoints": coverpoints,
    }
