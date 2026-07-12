"""Validate the 7 required JSON output files against schema + spec hard rules.

Spec §34: every coverage bin must have an explicit sampling_condition
Spec §37: bin names in functional_coverage.json must match coverage_bins.json
Spec §43: report.json must have stage flags + reproducible command
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from verif_agent.schemas import all_schemas


@pytest.fixture
def sample_outputs(tmp_path: Path):
    """Write a complete set of 7 valid JSONs under tmp_path; return the directory."""
    design = {
        "top": "dut",
        "rtl_files": ["rtl/a.v"],
        "ports": [
            {"name": "clk", "direction": "input", "width": 1,
             "protocol_group": "clk", "role": "clk"},
        ],
        "clock": [{"name": "clk", "width": 1, "period_ns": 10}],
        "reset": [{"name": "rst_n", "width": 1, "active_level": 0, "duration_cycles": 5}],
        "inferred_protocols": ["valid_ready_stream"],
        "primary_protocol": "valid_ready_stream",
    }
    skeleton = {
        "clock_reset": {"clock_source": "...", "reset_active_level": 0,
                         "reset_assert_cycles": 5, "reset_deassert_after_cycles": 2},
        "drivers": [{"name": "d", "interface": "x", "handles_ports": ["p"],
                      "handshake": "v", "backpressure_strategy": "wait"}],
        "monitors": [{"name": "m", "interface": "x", "sampling_edge": "posedge",
                      "samples": [{"port": "p", "condition": "x"}]}],
        "scoreboard": {"name": "s", "type": "transaction_level", "checks": ["eq"]},
        "dut_wiring": {"top": "dut", "wrapper_module": "tb_top", "files": []},
        "testbench_source": "generated_tb/tb_top.py",
    }
    constraints = {
        "seed": 12345, "num_seq": 5000,
        "random_variables": [{"name": "data", "kind": "rand", "width": 8,
                                "range": [0, 255], "dist": "uniform"}],
        "protocol_constraints": [],
        "coverage_feedback_updates": [],
    }
    bins = {
        "coverpoints": [
            {"name": "cp_payload", "bins": [
                {"name": "BIN_ZERO", "scenario": "all zeros",
                 "sampling_condition": "in_valid == 1 and in_ready == 1 and in_data == 0",
                 "covered": True, "hit_count": 5},
            ]},
        ],
    }
    functional = {
        "covered_bins": 1, "valid_bins": 1, "functional_coverage_pct": 100.0,
        "per_coverpoint": [
            {"name": "cp_payload", "bins": [
                {"name": "BIN_ZERO", "hit_count": 5, "covered": True},
            ]},
        ],
        "bin_summary": {"total": 1, "covered": 1, "uncovered": 0},
    }
    cov_result = {
        "line": 96.9, "branch": 80.0, "functional": 100.0, "combined_C": 92.76,
        "line_hits": 96, "line_total": 99, "branch_hits": 16, "branch_total": 20,
        "functional_hits": 1, "functional_total": 1,
    }
    report = {
        "stages": {"parse": "ok", "skeleton_gen": "ok",
                    "simulate": "ok", "coverage_collect": "ok"},
        "outputs": {"design_json": "design.json"},
        "coverage_summary": {"combined_C": 92.76},
        "reproducible_command": "./run.sh --rtl r --top dut --out o --seed 1 --num-seq 5000",
        "environment": {"python": "3.11", "tool": "verilator"},
        "failures": [],
    }

    files = {
        "design.json": design,
        "verification_skeleton.json": skeleton,
        "constraints.json": constraints,
        "coverage_bins.json": bins,
        "functional_coverage.json": functional,
        "coverage_result.json": cov_result,
        "report.json": report,
    }
    for name, obj in files.items():
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    return tmp_path


def test_schemas_validate(sample_outputs: Path):
    schemas = all_schemas()
    for name, schema in schemas.items():
        data = json.loads((sample_outputs / name).read_text(encoding="utf-8"))
        jsonschema.validate(data, schema)


def test_bin_names_must_match(sample_outputs: Path, tmp_path: Path):
    """Spec §37: functional_coverage.json bin names must match coverage_bins.json."""
    bins = json.loads((sample_outputs / "coverage_bins.json").read_text(encoding="utf-8"))
    func = json.loads((sample_outputs / "functional_coverage.json").read_text(encoding="utf-8"))
    bins_names = {(cp["name"], b["name"]) for cp in bins["coverpoints"] for b in cp["bins"]}
    func_names = {(cp["name"], b["name"]) for cp in func["per_coverpoint"] for b in cp["bins"]}
    assert bins_names == func_names


def test_covered_iff_hit_count_positive(sample_outputs: Path):
    """Spec §37: covered ⇔ hit_count > 0."""
    func = json.loads((sample_outputs / "functional_coverage.json").read_text(encoding="utf-8"))
    for cp in func["per_coverpoint"]:
        for b in cp["bins"]:
            assert b["covered"] == (b["hit_count"] > 0)


def test_every_bin_has_sampling_condition(sample_outputs: Path):
    """Spec §34."""
    bins = json.loads((sample_outputs / "coverage_bins.json").read_text(encoding="utf-8"))
    for cp in bins["coverpoints"]:
        for b in cp["bins"]:
            assert b.get("sampling_condition"), f"{cp['name']}/{b.get('name')} missing"
            assert isinstance(b["sampling_condition"], str)
            assert len(b["sampling_condition"]) >= 1


def test_report_has_stage_flags(sample_outputs: Path):
    """Spec §43: report.json must have all 4 stage flags."""
    report = json.loads((sample_outputs / "report.json").read_text(encoding="utf-8"))
    for key in ("parse", "skeleton_gen", "simulate", "coverage_collect"):
        assert key in report["stages"], f"missing stage {key}"


def test_report_has_reproducible_command(sample_outputs: Path):
    """Spec §43."""
    report = json.loads((sample_outputs / "report.json").read_text(encoding="utf-8"))
    assert "reproducible_command" in report
    assert isinstance(report["reproducible_command"], str)
    assert len(report["reproducible_command"]) > 0


def test_coverage_result_combined_C_computed(sample_outputs: Path):
    """C = 0.4*line + 0.3*branch + 0.3*functional."""
    cov = json.loads((sample_outputs / "coverage_result.json").read_text(encoding="utf-8"))
    expected = round(0.4 * cov["line"] + 0.3 * cov["branch"] + 0.3 * cov["functional"], 2)
    assert abs(cov["combined_C"] - expected) < 0.05


def test_no_sampling_condition_in_bin_rejected(sample_outputs: Path):
    bins = json.loads((sample_outputs / "coverage_bins.json").read_text(encoding="utf-8"))
    bins["coverpoints"][0]["bins"].append({"name": "BIN_BAD", "scenario": "no condition"})
    # writing → schema check should fail
    from jsonschema import ValidationError
    schemas = all_schemas()
    from verif_agent.schemas import schema_for
    with pytest.raises(ValidationError):
        jsonschema.validate(bins, schema_for("coverage_bins.json"))
