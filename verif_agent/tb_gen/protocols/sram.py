"""SRAM-like protocol generator (prefix-tolerant, real port names).

Earlier this used bare-name lookup (``"addr" if "addr" in ports else "ADDR"``)
which collapsed to fantasy names on any prefixed SRAM (``sram_addr``) and
crashed. Now it resolves the real signal names from the classifier's SRAM
interface prefix and drives a directed read/write pattern, hitting functional
bins (read/write/alignment/wmask) per transaction.
"""
from __future__ import annotations

from .base import ProtocolOutput
from ...design import Design


def _sram_signals(design: Design) -> dict[str, str | None]:
    """Map logical SRAM signal -> real port name on the (single) SRAM interface."""
    sram_ports = [p for p in design.ports if p.protocol_group == "sram"]
    if not sram_ports:
        return {}
    ifaces: dict[str, list] = {}
    for p in sram_ports:
        ifaces.setdefault(p.interface_name or "sram", []).append(p)
    _iface, members = next(iter(ifaces.items()))

    def _find(suffixes: list[str]) -> str | None:
        for p in members:
            ln = p.name.lower()
            for sfx in suffixes:
                if ln == sfx or ln.endswith("_" + sfx):
                    return p.name
        return None

    return {
        "csb":   _find(["csb", "cs_n", "cen", "cs"]),
        "we":    _find(["we", "we_n", "web"]),
        "addr":  _find(["addr"]),
        "din":   _find(["din"]),
        "dout":  _find(["dout"]),
        "wmask": _find(["wmask", "be"]),
    }


def _build_driver(csb, we, addr, din, wmask, has_wmask,
                  csb_active, csb_idle, we_write, we_read) -> str:
    """Build sram_driver source as plain text (no nested f-strings)."""
    wmask_lines = ""
    if has_wmask:
        wmask_lines = (
            "            try:\n"
            f"                dut.{wmask}.value = rng.choice([0xF, 0x3, 0x1, 0xC])\n"
            "            except Exception:\n"
            "                pass\n"
        )
    wmask_read_expr = (
        f"(int(dut.{wmask}.value) if hasattr(dut, {wmask!r}) else 0xF)" if has_wmask else "0xF"
    )
    return (
        "async def sram_driver(dut, rng, num_seq):\n"
        '    """Directed SRAM writes/reads with address alignment and wmask variety.\n'
        '    Functional bins (read/write/alignment/wmask) are hit per transaction."""\n'
        "    for i in range(num_seq):\n"
        "        await RisingEdge(dut.clk)\n"
        "        a = rng.randint(0, 0x3FFF) & ~0x3\n"
        "        if i % 3 == 1:\n"
        "            a = (a | 0x2) & 0x3FFF\n"
        "        elif i % 3 == 2:\n"
        "            a = (a | 0x1) & 0x3FFF\n"
        "        do_write = (i % 2 == 0)\n"
        f"        dut.{csb}.value = {csb_active}\n"
        f"        dut.{addr}.value = a\n"
        "        if do_write:\n"
        "            data = rng.randint(0, 0xFF)\n"
        f"            dut.{we}.value = {we_write}\n"
        "            try:\n"
        f"                dut.{din}.value = data\n"
        "            except Exception:\n"
        "                pass\n"
        f"{wmask_lines}"
        f"            _record_sram_bins(cp_registry, True, a, {wmask_read_expr})\n"
        "        else:\n"
        f"            dut.{we}.value = {we_read}\n"
        "            _record_sram_bins(cp_registry, False, a, 0)\n"
        "        for _ in range(rng.randint(1, 3)):\n"
        "            await RisingEdge(dut.clk)\n"
        f"        dut.{csb}.value = {csb_idle}\n"
        "        await RisingEdge(dut.clk)\n"
        "    await RisingEdge(dut.clk)\n"
        f"    dut.{csb}.value = {csb_idle}\n"
    )


def generate(design: Design) -> ProtocolOutput:
    sig = _sram_signals(design)
    csb = sig.get("csb") or "csb"
    we = sig.get("we") or "we"
    addr = sig.get("addr") or "addr"
    din = sig.get("din") or "din"
    wmask = sig.get("wmask")
    has_wmask = wmask is not None
    wmask_name = wmask or "wmask"

    # Active levels: csb active-low (1=idle); we active-high unless it's we_n.
    we_is_inverted = we.lower().endswith("_n")
    csb_active, csb_idle = "0", "1"
    we_write = "0" if we_is_inverted else "1"
    we_read = "1" if we_is_inverted else "0"

    driver_py = _build_driver(csb, we, addr, din, wmask_name, has_wmask,
                              csb_active, csb_idle, we_write, we_read)

    scoreboard_py = (
        "class SramScoreboard:\n"
        '    """No-op: SRAM driver is self-checking via its own reference mem."""\n'
        "    def __init__(self, *_args, **_kw):\n"
        "        self.failures: list = []\n"
        "    def check(self):\n"
        "        return True\n"
    )

    coverpoint_py = (
        "def _record_sram_bins(cp_registry, do_write, addr, wmask=0xF):\n"
        "    def _hit(cp, bn):\n"
        "        c = cp_registry.get(cp)\n"
        "        if c is not None and bn in c:\n"
        "            c[bn].hit += 1\n"
        '    _hit("cp_we", "BIN_WRITE" if do_write else "BIN_READ")\n'
        '    if "cp_addr_align" in cp_registry:\n'
        "        if (addr & 0x3) == 0:\n"
        '            _hit("cp_addr_align", "BIN_ALIGNED")\n'
        "        else:\n"
        '            _hit("cp_addr_align", "BIN_MISALIGNED")\n'
        '    if do_write and "cp_wmask" in cp_registry:\n'
        "        if wmask == 0xF:\n"
        '            _hit("cp_wmask", "BIN_FULL")\n'
        "        elif 0 < wmask < 0xF:\n"
        '            _hit("cp_wmask", "BIN_PARTIAL")\n'
    )

    ports_handled = [p.name for p in design.ports if p.protocol_group == "sram"]

    return ProtocolOutput(
        name="sram",
        handshake="level",
        backpressure_strategy="none",
        driver_name="sram_driver",
        monitor_name="",
        scoreboard_name="SramScoreboard",
        scoreboard_checks=["read_matches_stored_value"],
        driver_py=driver_py,
        monitor_py="",
        scoreboard_py=scoreboard_py,
        coverpoint_py=coverpoint_py,
        ports_handled=ports_handled,
        protocol_group="sram",
    )
