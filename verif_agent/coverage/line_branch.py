"""Parse Verilator's coverage.xml (and Icarus' coverage.dat) to per-file aggregates.

Returns unified (line_hits, line_total, branch_hits, branch_total). The XML
parser is the primary path; the .dat parser is a stub for Icarus fallback.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_verilator_xml(xml_path) -> tuple[int, int, int, int]:
    """Read Verilator's coverage.xml; return (line_hits, line_total, branch_hits, branch_total).

    Verilator coverage.xml schema (5.x):
      <document>
        <file id="...">
          <line coverage=".." count=".." />
          <branch coverage=".." count=".." />
        </file>
      </document>
    We sum 'count' per file, where each unique <line> / <branch> contributes 1 to total.
    """
    p = Path(xml_path)
    if not p.exists() or p.stat().st_size == 0:
        return 0, 0, 0, 0
    try:
        tree = ET.parse(p)
    except ET.ParseError:
        return 0, 0, 0, 0

    line_hits = line_total = branch_hits = branch_total = 0
    for f in tree.getroot().iter("file"):
        for child in f:
            tag = child.tag
            count_str = child.attrib.get("count", "0")
            try:
                count = int(count_str)
            except ValueError:
                count = 0
            if tag == "line":
                line_total += 1
                if count > 0:
                    line_hits += 1
            elif tag == "branch":
                branch_total += 1
                if count > 0:
                    branch_hits += 1
    return line_hits, line_total, branch_hits, branch_total


def parse_icarus_dat(dat_path) -> tuple[int, int, int, int]:
    """Best-effort Icarus coverage.dat parser. Format not stable across versions,
    so we conservatively return zeros with whatever hit count we can grok.

    Returns (line_hits, line_total, branch_hits, branch_total).
    """
    p = Path(dat_path)
    if not p.exists() or p.stat().st_size == 0:
        return 0, 0, 0, 0
    text = p.read_text(encoding="utf-8", errors="ignore")
    counts = re.findall(r"^\s*(\d+)\s*$", text, flags=re.MULTILINE)
    hits = sum(1 for c in counts if int(c) > 0)
    total = len(counts)
    # Map to line coverage (rough); branch not derivable from .dat alone.
    return hits, max(total, 1), 0, 0


def parse_verilator_dat(dat_path) -> tuple[int, int, int, int]:
    """Parse Verilator's native coverage.dat directly.

    Verilator 5.x emits a text database where each line is::

        C 'f<file>l<line>n<col>page v_line/<mod>o<kind>S<lines>h<inst>' <count>
        C '...                          v_branch/<mod>o<if|else>S<lines>...' <count>

    Each ``v_line`` record is one executable line; each ``v_branch`` record is
    one branch arm (an if-arm or else-arm of a conditional). count>0 means the
    line/arm was exercised. This is Verilator's REAL branch data — ``verilator_
    coverage --write-info`` drops it (emits only lcov DA: records), so parsing
    the .dat directly is the only way to get a non-zero branch figure under
    Verilator.

    The auto-generated wrapper (dut_inst.v) and tb_top are excluded — only real
    RTL source files carry meaningful DUT coverage.

    Returns (line_hits, line_total, branch_hits, branch_total).
    """
    p = Path(dat_path)
    if not p.exists() or p.stat().st_size == 0:
        return 0, 0, 0, 0
    text = p.read_text(encoding="utf-8", errors="ignore")

    # One C '...' count line per record. Fields are separated by control chars:
    #   C '\x01f\x02<path>\x01l\x02<line>\x01n\x02<col>\x01page\x02v_line/<mod>\x01o\x02<kind>\x01S\x02<lines>\x01h\x02<inst>' <count>
    # Capture file path, kind (v_line/v_branch), line number, and trailing count.
    rec = re.compile(
        r"\x01f\x02(.*?)\x01l\x02(\d+)\x01n\x02\d+\x01page\x02(v_line|v_branch)/[^']*?'\s+(\d+)",
    )
    line_hits = line_total = branch_hits = branch_total = 0
    for m in rec.finditer(text):
        path, line_no, kind, count_str = m.group(1), m.group(2), m.group(3), m.group(4)
        base = Path(path).name.lower()
        # Skip the generated wrapper / testbench artifacts — they are not DUT logic.
        if base in ("dut_inst.v", "tb_top.v", "tb_top.py"):
            continue
        try:
            count = int(count_str)
        except ValueError:
            count = 0
        if kind == "v_line":
            line_total += 1
            if count > 0:
                line_hits += 1
        elif kind == "v_branch":
            branch_total += 1
            if count > 0:
                branch_hits += 1
    return line_hits, max(line_total, 1), branch_hits, max(branch_total, 1)


def parse_verilator_info(info_path) -> tuple[int, int, int, int]:
    """Parse a Verilator-generated lcov ``.info`` file.

    ``verilator_coverage --write-info out.info coverage.dat`` emits standard
    lcov format. We ignore the auto-generated wrapper (``dut_inst.v``) and the
    ``tb_top`` itself — only the real RTL source files carry meaningful DUT
    line/branch coverage. We sum over every ``SF:`` section whose path is NOT
    the wrapper / testbench.

    Returns (line_hits, line_total, branch_hits, branch_total).
    """
    p = Path(info_path)
    if not p.exists() or p.stat().st_size == 0:
        return 0, 0, 0, 0
    text = p.read_text(encoding="utf-8", errors="ignore")

    line_hits = line_total = branch_hits = branch_total = 0
    in_file = False
    skip_file = False
    cur_file = None
    for raw in text.splitlines():
        if raw.startswith("SF:"):
            cur_file = raw[3:]
            # Skip generated wrapper / testbench artifacts — they are not DUT logic.
            base = Path(cur_file).name.lower()
            skip_file = base in ("dut_inst.v", "tb_top.v", "tb_top.py")
            in_file = True
            continue
        if raw == "end_of_record":
            in_file = False
            cur_file = None
            continue
        if not in_file or skip_file:
            continue
        if raw.startswith("DA:"):
            # DA:<line>,<count>[,<checksum>]
            parts = raw[3:].split(",")
            try:
                count = int(parts[1])
            except (IndexError, ValueError):
                continue
            line_total += 1
            if count > 0:
                line_hits += 1
        elif raw.startswith("BRDA:"):
            # BRDA:<line>,<block>,<branch>,<taken>  (taken "-" == 0/never)
            parts = raw[5:].split(",")
            try:
                taken = parts[3]
            except IndexError:
                continue
            branch_total += 1
            if taken not in ("-", "0", ""):
                branch_hits += 1
    return line_hits, max(line_total, 1), branch_hits, max(branch_total, 1)
