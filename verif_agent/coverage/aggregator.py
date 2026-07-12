"""Combined coverage computation: line, branch, functional, C.

Formula (spec §112 / §113):
  C = 0.4 * line + 0.3 * branch + 0.3 * functional
"""
from __future__ import annotations


def compute(
    line_hits: int, line_total: int,
    branch_hits: int, branch_total: int,
    functional_covered: int, functional_valid: int,
) -> dict:
    line = 100.0 * line_hits / max(line_total, 1)
    branch = 100.0 * branch_hits / max(branch_total, 1)
    functional = 100.0 * functional_covered / max(functional_valid, 1)
    combined = 0.4 * line + 0.3 * branch + 0.3 * functional
    return {
        "line": round(line, 2),
        "branch": round(branch, 2),
        "functional": round(functional, 2),
        "combined_C": round(combined, 2),
        "line_hits": int(line_hits),
        "line_total": int(line_total),
        "branch_hits": int(branch_hits),
        "branch_total": int(branch_total),
        "functional_hits": int(functional_covered),
        "functional_total": int(functional_valid),
    }
