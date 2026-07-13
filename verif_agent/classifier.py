"""Protocol classification.

Mutates Design.ports in place, filling protocol_group + role + interface_name,
and sets Design.inferred_protocols / Design.primary_protocol.

Pipeline:
  Pass 1 — detect global protocol membership
  Pass 2 — assign protocol_group / role per port (reverse-match signal names)
  Pass 3 — cluster remaining passive ports by name prefix
  Pass 4 — assign interface_name for every port
"""
from __future__ import annotations

import re
from collections import Counter

from .design import Design, Port


# ---------------------------------------------------------------------------
# Signal-name sets per protocol / channel
# ---------------------------------------------------------------------------

# AXI4 write-address channel
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
# Unified address channel (AXI address-only crossbar etc.): a* not split into ar/aw
_AXI_A_SIGNALS = {"avalid", "aready", "aaddr", "aprot", "aqos", "aid",
                  "aregion", "alock", "acache", "auser"}

# Map: channel_group_name → signal set  (used by _reverse_match)
_AXI_SIGNAL_MAP = {
    "axi_aw": _AXI_AW_SIGNALS,
    "axi_w":  _AXI_W_SIGNALS,
    "axi_b":  _AXI_B_SIGNALS,
    "axi_ar": _AXI_AR_SIGNALS,
    "axi_r":  _AXI_R_SIGNALS,
    "axi_a":  _AXI_A_SIGNALS,
}

_AXI_FULL_CHANNELS = (_AXI_AW_SIGNALS, _AXI_W_SIGNALS, _AXI_B_SIGNALS,
                       _AXI_AR_SIGNALS, _AXI_R_SIGNALS)

# Signals whose absence makes us lean AXI-Lite (no burst / no id)
_AXI_LITE_BURST_INDICATORS = {"awsize", "awburst", "awlen", "awid", "awlock",
                              "arsize", "arburst", "arlen", "arid", "arlock"}

# AXI-Stream
_AXIS_SIGNALS = {"tvalid", "tready", "tdata", "tlast", "tstrb", "tkeep",
                 "tid", "tdest", "tuser"}
_AXIS_SIGNAL_MAP = {"axis": _AXIS_SIGNALS}

# APB
_APB_CORE = {"psel", "penable", "pready", "pwrite"}
_APB_DATA = {"paddr", "pwdata", "prdata"}
_APB_OPTIONAL = {"pslverr", "pstrb", "pprot", "pclk"}
_APB_ALL = _APB_CORE | _APB_DATA | _APB_OPTIONAL
_APB_SIGNAL_MAP = {"apb": _APB_ALL}

# SRAM
_SRAM_CHIP_EN = {"csb", "cs_n", "cen", "cs"}
_SRAM_DATA_PORTS = {"we", "we_n", "web", "addr", "din", "dout", "wmask", "be"}

# Clock / Reset
_CLOCK_NAMES = {"clk", "clk_i", "hclk", "pclk", "aclk", "mclk", "sclk", "clock"}
_RESET_SUBSTR = ("reset", "rst", "arst", "nrst")


# ---------------------------------------------------------------------------
# Reverse-match: derive prefix from known signal names
# ---------------------------------------------------------------------------

def _reverse_match(name: str, signal_map: dict[str, set[str]]) -> tuple[str | None, str | None, str | None]:
    """Match a port name against known signal-name sets by suffix.

    Returns (prefix, signal_name, channel_group) or (None, None, None).

    Matching rule: name == signal  OR  name ends with '_' + signal.
    Longest signal name wins (avoids 'awvalid' matching 'valid').

    Examples (AXI signal_map):
        s_axi_awvalid  → ("s_axi", "awvalid", "axi_aw")
        m_axi_araddr   → ("m_axi",  "araddr",  "axi_ar")
        port_a_wstrb   → ("port_a", "wstrb",   "axi_w")
        awvalid        → ("",       "awvalid", "axi_aw")
        something_else → (None,     None,      None)
    """
    ln = name.lower()
    # Collect all (signal_name, group_name) pairs, sorted longest-first
    candidates: list[tuple[str, str]] = []
    for group_name, sigs in signal_map.items():
        for s in sigs:
            candidates.append((s, group_name))
    candidates.sort(key=lambda x: len(x[0]), reverse=True)

    for sig, group in candidates:
        if ln == sig or ln.endswith("_" + sig):
            if ln == sig:
                prefix = ""
            else:
                prefix = ln[:-len(sig)].rstrip("_")
            return prefix, sig, group
    return None, None, None


# ---------------------------------------------------------------------------
# Clock / Reset detection helpers
# ---------------------------------------------------------------------------

def _looks_like_reset(name: str) -> bool:
    ln = name.lower()
    if len(ln) > 20:
        return False
    # Suffix patterns: *_rst_out, *_rst, *_reset_n, etc.
    for sfx in ("_rst_out", "_rst_n", "_reset_out", "_reset_n",
                "_arst_out", "_arst_n", "_nrst_out", "_nrst_n"):
        if ln.endswith(sfx):
            return True
    for prefix in ("sys_", "s_", "h_", "p_", "a_", "sys", "h", "p", "s"):
        if ln.startswith(prefix):
            tail = ln[len(prefix):].lstrip("_")
            if tail and ("reset" in tail or "arst" in tail or "nrst" in tail
                         or tail.startswith("rst") or tail.startswith("rst_")):
                return True
    return "reset" in ln or "rst" in ln or "arst" in ln or "nrst" in ln


def _is_clock_port(p: Port) -> bool:
    ln = p.name.lower()
    if p.width != 1:
        return False
    # Exact match against known names
    if ln in _CLOCK_NAMES:
        return True
    # Suffix match: input_clk, output_clk, core_clk, etc.
    if ln.endswith("_clk") or ln.endswith("_clock"):
        return True
    return False


def _is_reset_port(p: Port) -> bool:
    if p.width != 1:
        return False
    return _looks_like_reset(p.name)


def _reset_active_level(p: Port) -> int:
    ln = p.name.lower()
    if ln.endswith("_n") or ln.endswith("n"):
        return 0
    return 1


# ---------------------------------------------------------------------------
# AXI signal direction (within a channel)
# ---------------------------------------------------------------------------

def _axi_signal_role(bare_name: str) -> str:
    """Return 'driver' or 'monitor' for a bare AXI signal name.

    -valid and -addr/size/len/burst/... are driver (master drives them).
    -ready is monitor (slave drives ready; we observe it).
    -data/rdata/wdata: direction depends on channel (handled per-channel below).
    """
    n = bare_name.lower()
    # AW / AR channel: everything except *ready is driver
    if n in ("awready", "arready", "aready"):
        return "monitor"
    if n.startswith(("aw", "ar", "a")):
        return "driver"
    # W channel: wvalid/wdata/wstrb/wlast are driver; wready is monitor
    if n == "wready":
        return "monitor"
    if n.startswith("w"):
        return "driver"
    # B channel: bvalid/bresp/bid are monitor; bready is driver
    if n == "bready":
        return "driver"
    if n.startswith("b"):
        return "monitor"
    # R channel: rvalid/rdata/rresp/rlast/rid are monitor; rready is driver
    if n == "rready":
        return "driver"
    if n.startswith("r"):
        return "monitor"
    return "passive"


# ---------------------------------------------------------------------------
# SRAM signal direction
# ---------------------------------------------------------------------------

def _sram_signal_direction(p_name: str) -> str:
    n = p_name.lower()
    if n in {"dout"}:
        return "monitor"
    if n in {"csb", "cs_n", "cen", "cs", "we", "we_n", "web", "addr", "din", "wmask", "be"}:
        return "driver"
    return "passive"


# ---------------------------------------------------------------------------
# APB signal direction
# ---------------------------------------------------------------------------

def _apb_signal_role(bare_name: str, direction: str) -> str:
    if bare_name in {"psel", "penable", "pwrite"}:
        return "driver"
    if bare_name in {"paddr", "pwdata", "pstrb", "pprot", "pclk"}:
        return "driver"
    if bare_name in {"pready", "pslverr"}:
        return "monitor"
    if bare_name == "prdata":
        return "monitor"
    return "passive"


# ---------------------------------------------------------------------------
# Stream signal direction
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pass 1: detect global protocol membership
# ---------------------------------------------------------------------------

def _axi_present(port_names: set[str], sigs: set[str]) -> int:
    return sum(1 for s in sigs if s in port_names)


def _has_axi_streams(port_names: set[str]) -> dict[str, bool]:
    """Check AXI channel presence robust to any prefix.

    Uses reverse-match: for each port, try to match against known AXI signals.
    """
    bare: set[str] = set()
    for n in port_names:
        _, sig, _ = _reverse_match(n, _AXI_SIGNAL_MAP)
        if sig:
            bare.add(sig)
    return {
        "aw": any(b in _AXI_AW_SIGNALS for b in bare),
        "w":  any(b in _AXI_W_SIGNALS  for b in bare),
        "b":  any(b in _AXI_B_SIGNALS  for b in bare),
        "ar": any(b in _AXI_AR_SIGNALS for b in bare),
        "r":  any(b in _AXI_R_SIGNALS  for b in bare),
        "a":  any(b in _AXI_A_SIGNALS  for b in bare),
    }


def _classify_axi(port_names: set[str]) -> tuple[bool, str]:
    """Return (is_axi, variant). variant in {'AXI4', 'AXI4-Lite', 'unknown'}.

    Reverse-match: strips any prefix by matching known signal suffixes.
    Recognises AXI if EITHER a complete write channel set (AW+W+B) OR a
    complete read channel set (AR+R) is present. Real IPs are frequently
    read-only or write-only adapters.
    """
    has = _has_axi_streams(port_names)
    write_ok = has["aw"] and has["w"] and has["b"]
    read_ok = has["ar"] and has["r"]
    addr_ok = has["a"]   # unified address-only channel (e.g. address crossbar)
    if not (write_ok or read_ok or addr_ok):
        return False, "unknown"

    bare: set[str] = set()
    for n in port_names:
        _, sig, _ = _reverse_match(n, _AXI_SIGNAL_MAP)
        if sig:
            bare.add(sig)
    has_burst = any(b in _AXI_LITE_BURST_INDICATORS for b in bare)
    if has_burst:
        return True, "AXI4"
    return True, "AXI4-Lite"


def _classify_apb(port_names: set[str]) -> bool:
    """APB: presence of the 4 core signals (psel/penable/pwrite/pready)."""
    bare: set[str] = set()
    for n in port_names:
        _, sig, _ = _reverse_match(n, _APB_SIGNAL_MAP)
        if sig:
            bare.add(sig)
    return _APB_CORE.issubset(bare)


def _classify_sram(port_names: set[str]) -> bool:
    has_ce = any(ce in port_names for ce in _SRAM_CHIP_EN)
    data_count = sum(1 for s in _SRAM_DATA_PORTS if s in port_names)
    return has_ce and data_count >= 3


def _classify_stream(port_names: set[str]) -> bool:
    """valid/ready stream: at least one {valid, ready, data} triple.

    Independent of AXI presence — a bridge design carries both.
    """
    def has_suffix(suffix: str) -> bool:
        return any(p.endswith(f"_{suffix}") for p in port_names)
    return has_suffix("valid") and has_suffix("ready") and has_suffix("data")


def _classify_axis_stream(port_names: set[str]) -> bool:
    """AXI-Stream: tvalid + tready + tdata all present (prefix-tolerant via reverse-match)."""
    bare: set[str] = set()
    for n in port_names:
        _, sig, _ = _reverse_match(n, _AXIS_SIGNAL_MAP)
        if sig:
            bare.add(sig)
    return {"tvalid", "tready", "tdata"} <= bare


# ---------------------------------------------------------------------------
# Pass 2: assign protocol_group / role per port
# ---------------------------------------------------------------------------

def _axi_signal_group(bare_name: str) -> tuple[str, str]:
    """Return (channel_group, role) for a bare AXI signal name.

    channel_group ∈ {axi_aw, axi_w, axi_b, axi_ar, axi_r, axi_a}.
    """
    n = bare_name.lower()
    # unified address channel first (aready would otherwise match "ar")
    if n in _AXI_A_SIGNALS:
        return "axi_a", _axi_signal_role(n)
    if n.startswith("aw") or n == "awid":
        return "axi_aw", _axi_signal_role(n)
    if n.startswith("w"):
        return "axi_w", _axi_signal_role(n)
    if n.startswith("b"):
        return "axi_b", _axi_signal_role(n)
    if n.startswith("ar") or n == "arid":
        return "axi_ar", _axi_signal_role(n)
    if n.startswith("r"):
        return "axi_r", _axi_signal_role(n)
    return "axi_unknown", "passive"


# ---------------------------------------------------------------------------
# Main classify function
# ---------------------------------------------------------------------------

def classify(design: Design) -> Design:
    """Mutate design in place; return it for convenience."""
    port_names_lower = {p.name.lower() for p in design.ports}

    # === Pass 1: detect global protocol membership ===
    is_axi, axi_variant = _classify_axi(port_names_lower)
    is_apb = _classify_apb(port_names_lower)
    is_sram = _classify_sram(port_names_lower)
    is_stream = _classify_stream(port_names_lower)
    is_axis = _classify_axis_stream(port_names_lower)

    protocols: list[str] = []
    if is_axi:
        protocols.append(axi_variant)
    if is_apb:
        protocols.append("APB")
    if is_sram:
        protocols.append("SRAM")
    if is_stream:
        protocols.append("valid_ready_stream")
    if is_axis:
        protocols.append("AXI-Stream")

    design.inferred_protocols = protocols
    design.primary_protocol = protocols[0] if protocols else ""

    # === Pass 2: assign protocol_group / role per port ===

    # For streams, classify the side (in/out) using port direction:
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

    for p in design.ports:
        ln = p.name.lower()

        # Clock
        if _is_clock_port(p):
            p.protocol_group = "clk"
            p.role = "clk"
            continue
        # Reset
        if _is_reset_port(p):
            p.protocol_group = "rst"
            p.role = "rst"
            continue

        # AXI (reverse-match)
        if is_axi:
            prefix, sig, chan_group = _reverse_match(p.name, _AXI_SIGNAL_MAP)
            if sig:
                chan, role = _axi_signal_group(sig)
                p.protocol_group = chan
                p.role = role
                continue

        # APB (reverse-match)
        if is_apb:
            _, sig, _ = _reverse_match(p.name, _APB_SIGNAL_MAP)
            if sig:
                p.protocol_group = "apb"
                p.role = _apb_signal_role(sig, p.direction)
                continue

        # SRAM
        if is_sram and ln in (_SRAM_CHIP_EN | _SRAM_DATA_PORTS):
            p.protocol_group = "sram"
            p.role = _sram_signal_direction(p.name)
            continue

        # valid/ready stream
        if is_stream and (ln in stream_in_names or ln in stream_out_names):
            side = "in" if ln in stream_in_names else "out"
            p.protocol_group = "stream_in" if side == "in" else "stream_out"
            p.role = _stream_signal_direction(p.name, side)
            continue

        # AXI-Stream (reverse-match)
        if is_axis:
            prefix, sig, _ = _reverse_match(p.name, _AXIS_SIGNAL_MAP)
            if sig:
                is_out = ln.startswith("m_axis_")
                p.protocol_group = "axis_out" if is_out else "axis_in"
                if is_out:
                    p.role = "driver" if sig == "tready" else "monitor"
                else:
                    p.role = "monitor" if sig == "tready" else "driver"
                continue

        # Default: passive
        p.protocol_group = "passive"
        p.role = "passive"

    # === Pass 3: cluster passive ports by name prefix ===
    _cluster_passive_ports(design)

    # === Pass 4: assign interface_name ===
    _assign_interface_names(design)

    return design


# ---------------------------------------------------------------------------
# Pass 3: cluster passive ports by name prefix
# ---------------------------------------------------------------------------

def _extract_prefix(name: str) -> str:
    """Extract the first underscore-delimited segment as prefix.

    Examples: cfg_fifo_base_addr → cfg, sts_empty → sts,
              input_clk → input, clk → clk (no underscore → empty).
    For names with no underscore, returns empty string (no meaningful prefix).
    """
    parts = name.lower().split("_")
    return parts[0] if len(parts) >= 2 else ""


def _cluster_passive_ports(design: Design) -> None:
    """Group passive ports by name prefix.

    Ports sharing the same prefix (first '_'-delimited segment) and with >= 2
    members are reclassified as custom:<prefix>. Smaller groups stay passive.
    """
    passive_ports = [p for p in design.ports if p.protocol_group == "passive"]
    if not passive_ports:
        return

    # Group by prefix
    prefix_groups: dict[str, list[Port]] = {}
    for p in passive_ports:
        pfx = _extract_prefix(p.name)
        if pfx:
            prefix_groups.setdefault(pfx, []).append(p)

    # Only reclassify groups with >= 2 ports
    for pfx, ports in prefix_groups.items():
        if len(ports) >= 2:
            for p in ports:
                p.protocol_group = f"custom:{pfx}"
                # Role: input → driver, output → monitor
                p.role = "driver" if p.direction == "input" else "monitor"


# ---------------------------------------------------------------------------
# Pass 4: assign interface_name
# ---------------------------------------------------------------------------

def _assign_interface_names(design: Design) -> None:
    """Assign interface_name to every port based on its protocol_group and prefix.

    Rules:
      - clk/rst: ""
      - AXI: prefix from reverse-match → interface_name
              e.g. "s_axi" for s_axi_awvalid, "m_axi" for m_axi_araddr, "axi" for awvalid
      - AXI-Stream: prefix from reverse-match
              e.g. "s_axis", "m_axis", "axis"
      - APB: prefix or "apb"
      - SRAM: "sram"
      - stream_in/stream_out: stem name (e.g. "in" from in_valid, "output" from output_valid)
      - custom:xxx: prefix raw name (e.g. "cfg" → will be overwritten by LLM later)
      - passive: ""
    """
    # First pass: for AXI/APB/AXIS, re-derive the prefix from reverse-match
    axi_ports_prefix: dict[int, str] = {}
    for p in design.ports:
        if p.protocol_group.startswith("axi_"):
            prefix, _, _ = _reverse_match(p.name, _AXI_SIGNAL_MAP)
            axi_ports_prefix[id(p)] = prefix if prefix else "axi"
        elif p.protocol_group.startswith("axis_"):
            prefix, _, _ = _reverse_match(p.name, _AXIS_SIGNAL_MAP)
            axi_ports_prefix[id(p)] = prefix if prefix else "axis"
        elif p.protocol_group == "apb":
            prefix, _, _ = _reverse_match(p.name, _APB_SIGNAL_MAP)
            axi_ports_prefix[id(p)] = prefix if prefix else "apb"

    # Second pass: assign
    for p in design.ports:
        if p.protocol_group in ("clk", "rst"):
            p.interface_name = ""
        elif p.protocol_group.startswith("axi_"):
            p.interface_name = axi_ports_prefix.get(id(p), "axi")
        elif p.protocol_group.startswith("axis_"):
            p.interface_name = axi_ports_prefix.get(id(p), "axis")
        elif p.protocol_group == "apb":
            p.interface_name = axi_ports_prefix.get(id(p), "apb")
        elif p.protocol_group == "sram":
            p.interface_name = "sram"
        elif p.protocol_group == "stream_in" or p.protocol_group == "stream_out":
            # Derive stem from port name: in_valid → "in", output_ctrl_ready → "output_ctrl"
            ln = p.name.lower()
            for sfx in ("valid", "ready", "data"):
                if ln.endswith(f"_{sfx}"):
                    p.interface_name = ln[: -len(sfx) - 1]
                    break
            else:
                p.interface_name = p.protocol_group
        elif p.protocol_group.startswith("custom:"):
            # Use the raw prefix — LLM will overwrite with semantic name later
            p.interface_name = p.protocol_group.split(":", 1)[1]
        elif p.protocol_group == "passive":
            p.interface_name = ""
        else:
            p.interface_name = ""


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

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
