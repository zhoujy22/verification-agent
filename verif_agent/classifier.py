"""Protocol classification.

Mutates Design.ports in place, filling protocol_group + role, and sets
Design.inferred_protocols / Design.primary_protocol.

Rules (in spec order — first match wins for a port's role):

  1. Clock:    name ∈ {clk, clk_i, hclk, pclk, aclk, mclk, sclk, clock}, width == 1
  2. Reset:    name starts with rst/reset/arst/nrst + active-level from _n suffix
  3. AXI4:     >= 5 aw* signals + w* + b* + ar* + r* all present (channels complete)
  4. AXI4-Lite: subset of (3) + no awburst/awlen/awsize
  5. SRAM:     csb|cs_n|cen present + >= 3 of {we, addr, din, dout, wmask, be}
  6. Stream:   {*_valid + *_ready + *_data} triplet + no AW/W channels
  7. Passive:  anything else (sample-only)
"""
from __future__ import annotations

import re
from collections import Counter

from .design import Design, Port


# AXI4 write-address channel (read also exists: ar*)
_AXI_AW_SIGNALS = {
    "awvalid", "awready", "awaddr", "awprot",
    "awsize", "awburst", "awlen", "awid",
    "awlock", "awcache", "awqos", "awregion", "awuser",
}
_AXI_W_SIGNALS = {"wvalid", "wready", "wdata", "wstrb", "wlast", "wid"}
_AXI_B_SIGNALS = {"bvalid", "bready", "bresp", "bid"}
_AXI_AR_SIGNALS = {
    "arvalid", "arready", "araddr", "arprot",
    "arsize", "arburst", "arlen", "arid",
    "arlock", "arcache", "arqos", "arregion", "aruser",
}
_AXI_R_SIGNALS = {"rvalid", "rready", "rdata", "rresp", "rlast", "rid"}

# Prefixes commonly seen in real RTL — many IP cores rename the AXI signals
# with a per-port prefix to disambiguate (m_axi_*, s_axi_*, m0_*).
_AXI_PREFIXES = ("m_axi_", "s_axi_", "m0_", "m1_", "s0_", "s1_", "axi_", "")

_AXI_FULL_CHANNELS = (_AXI_AW_SIGNALS, _AXI_W_SIGNALS, _AXI_B_SIGNALS, _AXI_AR_SIGNALS, _AXI_R_SIGNALS)

# Signals whose absence makes us lean AXI-Lite (no burst / no id)
_AXI_LITE_BURST_INDICATORS = {"awsize", "awburst", "awlen", "awid", "awlock",
                              "arsize", "arburst", "arlen", "arid", "arlock"}

_SRAM_CHIP_EN = {"csb", "cs_n", "cen", "cs"}
_SRAM_DATA_PORTS = {"we", "we_n", "web", "addr", "din", "dout", "wmask", "be", "din", "dout"}

# APB signal set (AMBA APB v2/v3) — Plan B.
_APB_CORE = {"psel", "penable", "pready", "pwrite"}
_APB_DATA = {"paddr", "pwdata", "prdata"}
_APB_OPTIONAL = {"pslverr", "pstrb", "pprot", "pclk"}
_APB_ALL = _APB_CORE | _APB_DATA | _APB_OPTIONAL
_APB_PREFIXES = ("m_apb_", "s_apb_", "p_", "apb_", "")


_CLOCK_NAMES = {"clk", "clk_i", "hclk", "pclk", "aclk", "mclk", "sclk", "clock"}
# Reset port names come in many flavors: rst, rst_n, resetn, reset_n, aresetn,
# arst, arstn, nrst, srst, prstn, sysrst, por_n, hresetn, s_reset_n, coldrst, ...
# Strategy: word is "reset-like" if it contains one of {rst, reset, arst, nrst}
# as a substring after an optional single-letter/s_word_/sys_ prefix, and the
# length is <= 12 chars (to avoid random matching longer names).
_RESET_SUBSTR = ("reset", "rst", "arst", "nrst")


def _looks_like_reset(name: str) -> bool:
    ln = name.lower()
    if len(ln) > 12:
        return False
    # Strip common prefix words
    for prefix in ("sys_", "s_", "h_", "p_", "a_", "sys", "h", "p", "s"):
        if ln.startswith(prefix):
            tail = ln[len(prefix):].lstrip("_")
            if tail and ("reset" in tail or "arst" in tail or "nrst" in tail or tail.startswith("rst") or tail.startswith("rst_")):
                return True
    return "reset" in ln or "rst" in ln or "arst" in ln or "nrst" in ln


def _is_clock_port(p: Port) -> bool:
    return p.width == 1 and p.name.lower() in _CLOCK_NAMES


def _is_reset_port(p: Port) -> bool:
    if p.width != 1:
        return False
    return _looks_like_reset(p.name)


def _reset_active_level(p: Port) -> int:
    ln = p.name.lower()
    if ln.endswith("_n") or ln.endswith("n"):
        return 0
    return 1


def _axi_present(port_names: set[str], sigs: set[str]) -> int:
    return sum(1 for s in sigs if s in port_names)


def _strip_axi_prefix(name: str) -> str:
    """Strip a known prefix and return the bare signal (e.g. m_axi_awaddr → awaddr)."""
    ln = name.lower()
    for pfx in _AXI_PREFIXES:
        if pfx and ln.startswith(pfx):
            return ln[len(pfx):]
    return ln


def _has_axi_streams(port_names: set[str]) -> dict[str, bool]:
    """Check AXI channel presence robust to known prefixes."""
    bare = {_strip_axi_prefix(n) for n in port_names}
    return {
        "aw": any(b in _AXI_AW_SIGNALS for b in bare),
        "w":  any(b in _AXI_W_SIGNALS  for b in bare),
        "b":  any(b in _AXI_B_SIGNALS  for b in bare),
        "ar": any(b in _AXI_AR_SIGNALS for b in bare),
        "r":  any(b in _AXI_R_SIGNALS  for b in bare),
    }


def _classify_axi(port_names: set[str]) -> tuple[bool, str]:
    """Return (is_axi, variant). variant ∈ {'AXI4', 'AXI4-Lite', 'unknown'}.

    Strips common prefixes (m_axi_*, s_axi_*, m0_*, etc.) before checking.
    """
    has = _has_axi_streams(port_names)
    if not all(has.values()):
        # Must have all five channels to claim AXI
        return False, "unknown"

    bare = {_strip_axi_prefix(n) for n in port_names}
    has_burst = any(b in _AXI_LITE_BURST_INDICATORS for b in bare)
    if has_burst:
        return True, "AXI4"
    return True, "AXI4-Lite"


def _strip_apb_prefix(name: str) -> str:
    ln = name.lower()
    for pfx in _APB_PREFIXES:
        if pfx and ln.startswith(pfx):
            return ln[len(pfx):]
    return ln


def _classify_apb(port_names: set[str]) -> bool:
    """APB: presence of the 4 core signals (psel/penable/pwrite/pready) — robust to prefix."""
    bare = {_strip_apb_prefix(n) for n in port_names}
    return _APB_CORE.issubset(bare)


def _classify_sram(port_names: set[str]) -> bool:
    has_ce = any(ce in port_names for ce in _SRAM_CHIP_EN)
    data_count = sum(1 for s in _SRAM_DATA_PORTS if s in port_names)
    return has_ce and data_count >= 3


def _classify_stream(port_names: set[str]) -> bool:
    """valid/ready stream: at least one {valid, ready, data} triple and no AW/W channel."""
    has = _has_axi_streams(port_names)
    if has["aw"] or has["w"] or has["b"]:
        return False
    # any *_valid, *_ready, *_data
    def has_suffix(suffix: str) -> bool:
        return any(p.endswith(f"_{suffix}") for p in port_names)
    return has_suffix("valid") and has_suffix("ready") and has_suffix("data")


def _sram_signal_direction(p_name: str) -> str:
    """Classify SRAM port as input (CS, WE, addr, din, wmask, be) vs output (dout)."""
    n = p_name.lower()
    if n in {"dout"}:
        return "monitor"
    if n in {"csb", "cs_n", "cen", "cs", "we", "we_n", "web", "addr", "din", "wmask", "be"}:
        return "driver"
    return "passive"


def _stream_signal_direction(p_name: str, side: str) -> str:
    """side ∈ {'in', 'out'}."""
    n = p_name.lower()
    if n.endswith("_valid"):
        return "driver" if side == "in" else "monitor"
    if n.endswith("_ready"):
        return "monitor" if side == "in" else "driver"
    if n.endswith("_data"):
        return "driver" if side == "in" else "monitor"
    return "passive"


def _axi_signal_group(p_name: str) -> tuple[str, str]:
    """Return (channel, role) for an AXI signal name."""
    n = p_name.lower()
    if n.startswith("aw") or n == "awid":
        return "axi_aw", "driver"
    if n.startswith("w"):
        return "axi_w", "driver"
    if n.startswith("b"):
        return "axi_b", "monitor"
    if n.startswith("ar") or n == "arid":
        return "axi_ar", "driver"
    if n.startswith("r"):
        return "axi_r", "monitor"
    return "axi_unknown", "passive"


def classify(design: Design) -> Design:
    """Mutate design in place; return it for convenience."""
    port_names_lower = {p.name.lower() for p in design.ports}

    # 1) Pass 1: detect global protocol membership
    is_axi, axi_variant = _classify_axi(port_names_lower)
    is_apb = _classify_apb(port_names_lower)
    is_sram = _classify_sram(port_names_lower)
    is_stream = _classify_stream(port_names_lower)

    protocols: list[str] = []
    if is_axi:
        protocols.append(axi_variant)
    if is_apb:
        protocols.append("APB")
    if is_sram:
        protocols.append("SRAM")
    if is_stream:
        protocols.append("valid_ready_stream")

    design.inferred_protocols = protocols
    design.primary_protocol = protocols[0] if protocols else ""

    # 2) Pass 2: assign protocol_group / role per port
    # For streams, classify the side (in/out) using port direction:
    #   input port with `{stem}_valid` / `{stem}_data` → DUT consumes ⇒ IN
    #   output port with `{stem}_valid` / `{stem}_data` → DUT produces ⇒ OUT
    #   the corresponding `_ready` is the *opposite* side.
    stem_side: dict[str, str] = {}

    if is_stream:
        for p in design.ports:
            ln = p.name.lower()
            for sfx in ("valid", "data"):
                if ln.endswith(f"_{sfx}"):
                    stem = ln[: -len(sfx) - 1]
                    side = "in" if p.direction == "input" else "out"
                    stem_side[stem] = side
                    break

    stream_in_names: set[str] = set()
    stream_out_names: set[str] = set()
    if is_stream:
        for p in design.ports:
            ln = p.name.lower()
            for sfx in ("valid", "ready", "data"):
                if ln.endswith(f"_{sfx}"):
                    stem = ln[: -len(sfx) - 1]
                    side = stem_side.get(stem)
                    if side is None:
                        side = "in" if p.direction == "input" else "out"
                    if side == "in":
                        stream_in_names.add(p.name)
                    else:
                        stream_out_names.add(p.name)
                    break

    # Build a stripped-name lookup so prefix-tolerant role assignment works.
    axi_bare_to_orig: dict[str, str] = {_strip_axi_prefix(p.name): p.name for p in design.ports}
    apb_bare_to_orig: dict[str, str] = {_strip_apb_prefix(p.name): p.name for p in design.ports}

    for p in design.ports:
        ln = p.name.lower()
        if _is_clock_port(p):
            p.protocol_group = "clk"
            p.role = "clk"
            continue
        if _is_reset_port(p):
            p.protocol_group = "rst"
            p.role = "rst"
            continue

        # AXI: match either the full name OR the bare (prefix-stripped) name.
        if is_axi:
            bare = _strip_axi_prefix(p.name)
            if bare in (set(_AXI_AW_SIGNALS) | set(_AXI_W_SIGNALS) | set(_AXI_B_SIGNALS)
                        | set(_AXI_AR_SIGNALS) | set(_AXI_R_SIGNALS)):
                chan, role = _axi_signal_group(bare)
                p.protocol_group = chan
                p.role = role
                continue

        # APB: match core + optional signals (prefix-tolerant).
        if is_apb:
            bare = _strip_apb_prefix(p.name)
            if bare in _APB_ALL:
                p.protocol_group = "apb"
                p.role = _apb_signal_role(bare, p.direction)
                continue

        if is_sram and ln in (_SRAM_CHIP_EN | _SRAM_DATA_PORTS):
            p.protocol_group = "sram"
            p.role = _sram_signal_direction(p.name)
            continue

        if is_stream and (ln in stream_in_names or ln in stream_out_names):
            side = "in" if ln in stream_in_names else "out"
            p.protocol_group = "stream_in" if side == "in" else "stream_out"
            p.role = _stream_signal_direction(p.name, side)
            continue

        p.protocol_group = "passive"
        p.role = "passive"

    return design


def _apb_signal_role(bare_name: str, direction: str) -> str:
    """APB driver/monitor split: master drives psel/penable/pwrite/paddr/pwdata/pstrb/pprot;
    slave drives pready/pslverr; prdata is monitored."""
    if bare_name in {"psel", "penable", "pwrite"}:
        return "driver"
    if bare_name in {"paddr", "pwdata", "pstrb", "pprot", "pclk"}:
        return "driver"
    if bare_name in {"pready", "pslverr"}:
        return "monitor"
    if bare_name == "prdata":
        return "monitor"
    return "passive"


def infer_stream_sides(design: Design) -> tuple[set[str], set[str]]:
    """Public helper: stem → side map for downstream generators."""
    stems_in: set[str] = set()
    stems_out: set[str] = set()
    for p in design.ports:
        if p.protocol_group == "stream_in":
            stems_in.add(p.name)
        elif p.protocol_group == "stream_out":
            stems_out.add(p.name)
    return stems_in, stems_out
