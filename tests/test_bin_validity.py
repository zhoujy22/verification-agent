"""Bin validity test — gates bins without sampling_condition out of the count.

Spec §34: '每个 bin 必须有明确的采样条件'.
Spec §116: '仅把随机值逐个枚举成 bin、无法由测试采样证明命中的 bin，不计入有效总数'.
"""
from __future__ import annotations

from verif_agent.classifier import classify
from verif_agent.coverage import reconcile_with_bins
from verif_agent.coverage_definer import _enforce_sampling_condition
from verif_agent.design import Design, Port


def _stream_design() -> Design:
    return Design(top="x", ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="in_valid", direction="input", width=1),
        Port(name="in_ready", direction="output", width=1),
        Port(name="in_data", direction="input", width=8),
        Port(name="out_valid", direction="output", width=1),
        Port(name="out_ready", direction="input", width=1),
        Port(name="out_data", direction="output", width=8),
    ])


def test_missing_sampling_condition_rejected():
    with __import__("pytest").raises(ValueError):
        _enforce_sampling_condition({"name": "BAD_BIN", "scenario": "x"})


def test_reconcile_drops_bins_without_sampling_condition():
    bins = {
        "coverpoints": [
            {"name": "cp_x", "bins": [
                _enforce_sampling_condition({"name": "OK_BIN", "scenario": "s",
                                              "sampling_condition": "v"}),
                {"name": "BAD_BIN", "scenario": "no condition"},  # no sampling_condition!
            ]},
        ],
    }
    classify(_stream_design())
    raw = {"cp_x": {"OK_BIN": {"hit_count": 3, "covered": True},
                    "BAD_BIN": {"hit_count": 0, "covered": False}}}
    out = reconcile_with_bins(raw, bins)
    valid = out["valid_bins"]
    assert valid == 1, "BAD_BIN must NOT count toward valid_bins"
