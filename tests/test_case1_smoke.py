"""End-to-end smoke test for public_dataset/case1 (stream DUT).

Skips simulator execution if verilator/iverilog is unavailable;
still verifies the JSON outputs are produced.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from verif_agent.pipeline import run as pipeline_run

REPO_ROOT = Path(__file__).resolve().parents[1]
RTL = REPO_ROOT / "public_dataset" / "case1" / "rtl"


@pytest.mark.skipif(not (RTL.exists() and (RTL / "stream_dut.v").exists()),
                     reason="case1 RTL not present")
def test_pipeline_produces_required_outputs(tmp_path: Path):
    out_dir = tmp_path / "case1_out"
    result = pipeline_run(
        rtl_dir=str(RTL),
        top="stream_dut",
        out_dir=str(out_dir),
        seed=1,
        num_seq=200,        # short for speed; spec default is 5000
        timeout_sec=120,
    )

    # Even on simulator failure, the JSON set must exist.
    for fname in (
        "design.json",
        "verification_skeleton.json",
        "constraints.json",
        "coverage_bins.json",
        "functional_coverage.json",
        "coverage_result.json",
        "report.json",
    ):
        assert (out_dir / fname).exists(), f"missing {fname}"

    design = json.loads((out_dir / "design.json").read_text(encoding="utf-8"))
    assert design["top"] == "stream_dut"
    assert design["primary_protocol"] == "valid_ready_stream"

    skeleton = json.loads((out_dir / "verification_skeleton.json").read_text(encoding="utf-8"))
    assert skeleton["drivers"], "drivers must be non-empty per spec"
    assert skeleton["monitors"], "monitors must be non-empty per spec"

    bins = json.loads((out_dir / "coverage_bins.json").read_text(encoding="utf-8"))
    cp_names = {cp["name"] for cp in bins["coverpoints"]}
    assert {"cp_payload", "cp_backpressure", "cp_idle"} <= cp_names

    constraints = json.loads((out_dir / "constraints.json").read_text(encoding="utf-8"))
    assert constraints["seed"] == 1
    assert constraints["num_seq"] == 200

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    for stage in ("parse", "skeleton_gen"):
        assert report["stages"][stage] == "ok"
    assert "reproducible_command" in report

    cov = json.loads((out_dir / "coverage_result.json").read_text(encoding="utf-8"))
    assert "combined_C" in cov
    assert 0 <= cov["combined_C"] <= 100
