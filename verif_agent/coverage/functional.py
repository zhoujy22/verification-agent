"""Parse cocotb's functional_cov.json and reconcile with the original bins definition."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def parse_cocotb_json(json_path) -> dict:
    """Read the per-bin hit counts emitted by cocotb testbench.

    Expected shape:
        {
          "cp_awburst": {"BIN_FIXED": {"hit_count": 5, "covered": true}, ...},
          ...
        }
    """
    p = Path(json_path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("could not parse functional_cov.json: %s", exc)
        return {}


def reconcile_with_bins(raw: dict, bins_def: dict) -> dict:
    """Align the cocotb output to the original coverage_bins.json.

    Returns:
      {
        "covered_bins": int,
        "valid_bins": int,
        "functional_coverage_pct": float,
        "per_coverpoint": [
          {"name": cp, "bins": [{"name": bin, "hit_count": int, "covered": bool}]},
          ...
        ]
      }

    The `coverage_bins.json`'s cp/bin hierarchy is authoritative. Any bin without
    a sampling condition is dropped (per spec).
    """
    per_coverpoint: list[dict] = []
    covered = 0
    valid = 0
    for cp in bins_def.get("coverpoints", []):
        cp_name = cp["name"]
        bins_out = []
        for b in cp.get("bins", []):
            if not b.get("sampling_condition"):
                # Drop bin per spec
                continue
            bin_name = b["name"]
            entry = raw.get(cp_name, {}).get(bin_name, {})
            hit_count = int(entry.get("hit_count", 0))
            bin_covered = hit_count > 0
            valid += 1
            if bin_covered:
                covered += 1
            bins_out.append({"name": bin_name, "hit_count": hit_count, "covered": bin_covered})
        per_coverpoint.append({"name": cp_name, "bins": bins_out})

    pct = 100.0 * covered / max(valid, 1)
    return {
        "covered_bins": covered,
        "valid_bins": valid,
        "functional_coverage_pct": round(pct, 2),
        "per_coverpoint": per_coverpoint,
        "bin_summary": {"total": valid, "covered": covered, "uncovered": valid - covered},
    }
