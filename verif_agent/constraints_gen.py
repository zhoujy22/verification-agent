"""Constraint-random test-strategy generator.

Per spec:
  - fixed seed
  - num_seq == 5000
  - random variables list with ranges/distributions
  - protocol-level constraints
  - coverage_feedback_updates[] (filled later by feedback.py)

The generator is deterministic in (seed, design, num_seq).
"""
from __future__ import annotations

import hashlib
from typing import Any

from .design import Design


SPEC_NUM_SEQ = 5000


def _hash_to_int(*vals: Any) -> int:
    h = hashlib.sha256()
    for v in vals:
        h.update(repr(v).encode())
        h.update(b"|")
    return int.from_bytes(h.digest()[:4], "big")


def _rng_choices(seed: int, scope: str) -> int:
    """Deterministic seed-derivative RNG for stable dist/weight shaping."""
    return (seed ^ _hash_to_int(scope)) & 0x7FFFFFFF


def _port_max(port_name: str, width: int) -> int:
    if width <= 0:
        return 1
    return (1 << width) - 1


def generate(design: Design, seed: int, num_seq: int = SPEC_NUM_SEQ) -> dict:
    """Produce a `constraints.json`-shaped dict."""
    random_vars: list[dict[str, Any]] = []
    protocol_constraints: list[dict[str, Any]] = []

    # Pull driver-side signals per protocol_group.
    driver_ports = [p for p in design.ports if p.role == "driver"]

    rng = _rng_choices(seed, "vars")

    for p in driver_ports:
        width = max(p.width, 1)
        max_val = _port_max(p.name, width)
        var: dict[str, Any] = {
            "name": p.name,
            "kind": "rand",
            "width": width,
            "range": [0, max_val],
            "dist": "uniform",
            "protocol_constraint": "",
            "signal_group": p.protocol_group,
        }

        # AXI addr alignment + wstrb partial-bias
        ln = p.name.lower()
        if p.protocol_group == "axi_aw" and ln == "awaddr":
            var["dist"] = "uniform"
            var["align"] = 4
            var["protocol_constraint"] = "axi4_addr_align_4"
        elif p.protocol_group == "axi_ar" and ln == "araddr":
            var["dist"] = "uniform"
            var["align"] = 4
            var["protocol_constraint"] = "axi4_addr_align_4"
        elif ln == "wstrb":
            var["dist"] = "weighted"
            var["weights"] = {
                "0xF": 60, "0x1": 5, "0x2": 5, "0x4": 5, "0x8": 5,
                "0x3": 4, "0xC": 4, "0x0": 12,
            }
            var["protocol_constraint"] = "axi4_wstrb_legal"
        elif ln == "awburst":
            var["dist"] = "weighted"
            var["weights"] = {"INCR": 70, "FIXED": 20, "WRAP": 10}
            var["protocol_constraint"] = "axi4_burst_type"
        elif ln == "awsize":
            var["dist"] = "weighted"
            var["weights"] = {"1B": 5, "2B": 5, "4B": 80, "8B": 5, "16B": 5}
            var["protocol_constraint"] = "axi4_size_in_range"
        elif p.protocol_group in ("stream_in",):
            var["protocol_constraint"] = "stream_in_data_random"

        random_vars.append(var)

    # Global-level protocol constraints
    if any(g.startswith("axi") for g in {p.protocol_group for p in design.ports}):
        protocol_constraints.append({
            "name": "no_excessive_outstanding",
            "expr": "num_inflight_reads <= 4 && num_inflight_writes <= 4",
        })

    return {
        "schema_version": 1,
        "case_name": design.top,
        "seed": int(seed),
        "num_seq": int(num_seq),
        "random_variables": random_vars,
        "protocol_constraints": protocol_constraints,
        "coverage_feedback_updates": [],
        "rng_derivative_seed": rng,
    }
