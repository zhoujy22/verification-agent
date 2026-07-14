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


def _axi_read_caps(design: Design) -> tuple[bool, bool]:
    """Return (has_read, has_write) from classifier channel annotations."""
    groups = {p.protocol_group for p in design.ports}
    has_read = "axi_ar" in groups and "axi_r" in groups
    has_write = "axi_aw" in groups and "axi_w" in groups and "axi_b" in groups
    return has_read, has_write


def _axi_bins(design: Design) -> list[dict]:
    """AXI4 / AXI4-Lite functional bins, scoped to the channels the DUT actually has.

    Earlier versions always emitted write-strobe + write-response bins even for
    read-only adapters (case1 has only AR+R), so those bins could never hit and
    functional coverage was stuck near 0. Now bins are generated per the
    classifier's has_read/has_write capability, and each bin is a transaction
    FEATURE (length range / size / address alignment / burst type / backpressure)
    that the cocotb driver records directly when it issues the transaction —
    matching the reference testbench's FunctionalCoverage approach.
    """
    has_read, has_write = _axi_read_caps(design)
    coverpoints: list[dict] = []

    def _cp(name: str, bins: list[tuple[str, str, str]]) -> None:
        coverpoints.append({
            "name": name,
            "bins": [
                _enforce_sampling_condition({"name": bn, "scenario": sc, "sampling_condition": cond})
                for bn, sc, cond in bins
            ],
        })

    # Channel-selection helper: use ar*/r* for read-only, aw*/b* for write-only,
    # ar*/r* when both (read transactions carry the length/size/addr variety).
    ar, rresp = ("arvalid", "rresp") if has_read else ("awvalid", "bresp")

    if has_read or has_write:
        _cp("cp_burst_length", [
            ("BIN_LEN_1",      "single-beat burst (len=0)",        f"{ar} handshake and burst length == 1"),
            ("BIN_LEN_2_4",    "short burst (len 2-4)",             f"{ar} handshake and 2 <= burst length <= 4"),
            ("BIN_LEN_5_16",   "medium burst (len 5-16)",           f"{ar} handshake and 5 <= burst length <= 16"),
            ("BIN_LEN_17_64",  "long burst (len 17-64)",            f"{ar} handshake and 17 <= burst length <= 64"),
            ("BIN_LEN_65_256", "very long burst (len 65-256)",      f"{ar} handshake and 65 <= burst length <= 256"),
        ])

        _cp("cp_burst_size", [
            ("BIN_SIZE_1B",  "1-byte transfer size",  f"{ar} handshake and arsize == 0"),
            ("BIN_SIZE_2B",  "2-byte transfer size",  f"{ar} handshake and arsize == 1"),
            ("BIN_SIZE_4B",  "4-byte transfer size",  f"{ar} handshake and arsize == 2"),
            ("BIN_SIZE_8B",  "8-byte transfer size",  f"{ar} handshake and arsize == 3"),
        ])

        _cp("cp_addr_alignment", [
            ("BIN_ALIGNED_4",   "4-byte aligned address",   f"{ar} handshake and (addr & 0x3) == 0"),
            ("BIN_ALIGNED_2",   "2-byte aligned address",   f"{ar} handshake and (addr & 0x1) == 0 and (addr & 0x3) != 0"),
            ("BIN_UNALIGNED",   "unaligned address",        f"{ar} handshake and (addr & 0x1) != 0"),
            ("BIN_NEAR_4K",     "address near 4K boundary", f"{ar} handshake and addr % 4096 >= 4032"),
        ])

        _cp("cp_burst_type", [
            ("BIN_FIXED", "fixed burst",   f"{ar} handshake and arburst == 0"),
            ("BIN_INCR",  "increment burst", f"{ar} handshake and arburst == 1"),
            ("BIN_WRAP",  "wrap burst",    f"{ar} handshake and arburst == 2"),
        ])

    if has_read:
        _cp("cp_read_response", [
            ("BIN_R_OKAY",   "read OKAY response",   f"rvalid handshake and {rresp} == 0"),
            ("BIN_R_EXOKAY", "read EXOKAY response", f"rvalid handshake and {rresp} == 1"),
            ("BIN_R_SLVERR", "read SLVERR response", f"rvalid handshake and {rresp} == 2"),
        ])
    if has_write:
        _cp("cp_write_response", [
            ("BIN_B_OKAY",   "write OKAY response",   "bvalid handshake and bresp == 0"),
            ("BIN_B_EXOKAY", "write EXOKAY response", "bvalid handshake and bresp == 1"),
            ("BIN_B_SLVERR", "write SLVERR response", "bvalid handshake and bresp == 2"),
        ])

    _cp("cp_backpressure", [
        ("BIN_AR_STALL",  "AR valid stalled before ready",  f"{ar} asserted for >1 cycle before {('arready' if has_read else 'awready')}"),
        ("BIN_R_STALL",   "R ready backpressure applied",   "rready deasserted while rvalid high"),
        ("BIN_NO_STALL",  "single-cycle handshake",         f"{ar} handshake completes in 1 cycle"),
    ])

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


def _axis_bins(design: Design) -> list[dict]:
    """AXI-Stream functional bins (tvalid/tready/tdata + tlast).

    Bins mirror the transaction features the cocotb driver records per frame
    (length range / payload content / last beat / backpressure), so a
    FIFO/adapter DUT (case2) gets meaningful functional coverage instead of the
    single sampler-tick bin the generic path emits.
    """
    coverpoints: list[dict] = []

    def _cp(name: str, bins: list[tuple[str, str, str]]) -> None:
        coverpoints.append({
            "name": name,
            "bins": [
                _enforce_sampling_condition({"name": bn, "scenario": sc, "sampling_condition": cond})
                for bn, sc, cond in bins
            ],
        })

    _cp("cp_axis_frame_length", [
        ("BIN_LEN_1",     "single-beat frame",        "tvalid handshake and frame length == 1"),
        ("BIN_LEN_2_4",   "short frame (2-4 beats)",   "tvalid handshake and 2 <= frame length <= 4"),
        ("BIN_LEN_5_16",  "medium frame (5-16)",       "tvalid handshake and 5 <= frame length <= 16"),
        ("BIN_LEN_17_64", "long frame (17-64)",        "tvalid handshake and 17 <= frame length <= 64"),
    ])

    _cp("cp_axis_payload", [
        ("BIN_ZERO", "all-zero payload frame",  "tvalid handshake and every tdata byte == 0"),
        ("BIN_MAX",  "all-ones payload frame",  "tvalid handshake and every tdata byte == 0xFF"),
        ("BIN_MIX",  "mixed payload frame",     "tvalid handshake and payload has mixed values"),
    ])

    _cp("cp_axis_last", [
        ("BIN_MID",  "mid-packet beat",  "tvalid handshake and tlast == 0"),
        ("BIN_LAST", "packet last beat", "tvalid handshake and tlast == 1"),
    ])

    _cp("cp_axis_backpressure", [
        ("BIN_NO_BP", "full throughput (no stall)", "tvalid == 1 and tready == 1"),
        ("BIN_BP",    "backpressure applied",       "tvalid == 1 and tready == 0"),
    ])

    _cp("cp_axis_idle", [
        ("BIN_ACTIVE", "stream active (handshake fires)", "tvalid == 1 and tready == 1"),
        ("BIN_IDLE",   "stream idle",                     "tvalid == 0"),
    ])

    return coverpoints


def _addr_decoder_bins(design: Design) -> list[dict]:
    """Functional bins for a unified-address (a*) AXI decoder/crossbar (case5).

    Such DUTs have no data channels — they route an address request to one of
    N master ports (m_select) and propagate completion. Bins track which master
    is selected, decode-error, and the address handshake itself.
    """
    coverpoints: list[dict] = []

    def _cp(name: str, bins: list[tuple[str, str, str]]) -> None:
        coverpoints.append({
            "name": name,
            "bins": [
                _enforce_sampling_condition({"name": bn, "scenario": sc, "sampling_condition": cond})
                for bn, sc, cond in bins
            ],
        })

    _cp("cp_addr_handshake", [
        ("BIN_A_VALID",  "address request asserted", "s_axi_avalid == 1"),
        ("BIN_A_ACCEPT", "address request accepted", "s_axi_avalid == 1 and s_axi_aready == 1"),
        ("BIN_A_STALL",  "address request stalled",  "s_axi_avalid == 1 and s_axi_aready == 0"),
    ])

    _cp("cp_decode_select", [
        ("BIN_M_SELECT", "a master selected (m_select valid)",     "m_select is driven to a valid master index"),
        ("BIN_M_AVALID", "request forwarded to master (m_axi_avalid)", "m_axi_avalid == 1"),
        ("BIN_DECERR",   "decode error (no master matched)",       "m_wc_decerr == 1 or m_rc_decerr == 1"),
    ])

    _cp("cp_completion", [
        ("BIN_WC", "write completion routed",     "m_wc_valid == 1 and m_wc_ready == 1"),
        ("BIN_RC", "read completion routed",      "m_rc_valid == 1 and m_rc_ready == 1"),
        ("BIN_CPL", "completion returned to slave", "s_cpl_valid == 1"),
    ])

    return coverpoints


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


def _is_addr_decoder(design: Design) -> bool:
    """True if the DUT is a unified-address (a*) AXI decoder with no data channels."""
    groups = {p.protocol_group for p in design.ports}
    return "axi_a" in groups and not ({"axi_ar", "axi_aw", "axi_w", "axi_r", "axi_b"} & groups)


def define(design: Design) -> dict:
    """Return a `coverage_bins.json`-shaped dict, populated by protocol family."""
    coverpoints: list[dict] = []

    # Unified-address decoder/crossbar (case5): a* channel only, no data path.
    # It falls to the generic driver but gets decoder-specific functional bins.
    if _is_addr_decoder(design):
        coverpoints.extend(_addr_decoder_bins(design))

    protos = design.inferred_protocols or ([design.primary_protocol] if design.primary_protocol else [])
    for proto in protos:
        if proto in ("AXI4", "AXI4-Lite"):
            coverpoints.extend(_axi_bins(design))
        elif proto == "SRAM":
            coverpoints.extend(_sram_bins(design))
        elif proto == "valid_ready_stream":
            coverpoints.extend(_stream_bins(design))
        elif proto == "APB":
            coverpoints.extend(_apb_bins(design))
        elif proto == "AXI-Stream":
            coverpoints.extend(_axis_bins(design))

    # If no recognized protocol, fall back to a single sampler-tick bin so we
    # at least record one valid bin (per spec §34 — must have sampling_condition).
    if not coverpoints:
        coverpoints.extend(_generic_bins(design))

    return {
        "schema_version": 1,
        "case_name": design.top,
        "coverpoints": coverpoints,
    }
