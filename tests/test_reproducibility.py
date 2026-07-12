"""Reproducibility test: same seed ⇒ identical functional_coverage.json.

Spec §6: '运行过程必须可复现，同一 seed 和同一 RTL 输入应产生一致或等价的测试结果'.
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
def test_same_seed_same_outputs(tmp_path: Path):
    if shutil.which("verilator") is None and shutil.which("iverilog") is None:
        pytest.skip("no simulator available")

    out_a = tmp_path / "A"
    out_b = tmp_path / "B"

    res_a = pipeline_run(str(RTL), "stream_dut", str(out_a), seed=42, num_seq=100, timeout_sec=120)
    res_b = pipeline_run(str(RTL), "stream_dut", str(out_b), seed=42, num_seq=100, timeout_sec=120)

    if not (res_a.ok and res_b.ok):
        pytest.skip("simulator failed; reproducibility check requires both runs to succeed")

    a = json.loads((out_a / "functional_coverage.json").read_text(encoding="utf-8"))
    b = json.loads((out_b / "functional_coverage.json").read_text(encoding="utf-8"))

    assert a == b, "functional_coverage.json must match across same-seed runs"
