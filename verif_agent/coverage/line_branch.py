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
