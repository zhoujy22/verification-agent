"""End-to-end pipeline orchestrator.

Per spec §44: ./run.sh --rtl DIR --top NAME --out DIR --seed N --num-seq 5000
must complete without manual intervention. This module wires every stage.

Pipeline:
   parse → classify → coverage_definer → constraints_gen → render →
   simulate (Verilator→Icarus fallback) → collect coverage →
   feedback (max 2 iterations) → reporter.write_all
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .classifier import classify
from .constraints_gen import generate as generate_constraints
from .coverage import (
    compute_combined,
    parse_cocotb_json,
    parse_icarus_dat,
    parse_verilator_xml,
    reconcile_with_bins,
)
from .coverage_definer import define as define_bins
from .design import Design
from .feedback import MAX_ITER, TARGET_C, adjust as feedback_adjust
from .reporter import ReporterInputs, write_all
from .rtl_parser import resolve as resolve_design
from .sim import run_simulation
from .tb_gen.render import render as render_tb


log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    ok: bool
    error: str = ""
    stages: dict = field(default_factory=dict)
    cov_combined: float = 0.0


def run(rtl_dir: str, top: str, out_dir: str, seed: int, num_seq: int = 5000,
        timeout_sec: int = 600) -> PipelineResult:
    """Run the full pipeline. Writes all 7 JSON files to out_dir."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "generated_tests").mkdir(exist_ok=True)

    stages = {"parse": "pending", "skeleton_gen": "pending", "simulate": "pending", "coverage_collect": "pending"}
    failures: list[str] = []

    try:
        # Stage 1: parse + classify
        design = resolve_design(rtl_dir, top)
        classify(design)
        stages["parse"] = "ok"

        # Stage 2: coverpoints + constraints
        bins = define_bins(design)
        constraints = generate_constraints(design, seed=seed, num_seq=num_seq)

        # Stage 3: render tb
        rend = render_tb(design, constraints, bins, out)
        stages["skeleton_gen"] = "ok"

        # Stage 4: simulate
        try:
            sim_result, tool = run_simulation(rend.tb_dir, seed=seed, num_seq=num_seq, timeout_sec=timeout_sec)
            stages["simulate"] = "ok" if sim_result.ok else "fail"
        except Exception as exc:
            log.warning("simulator failure: %s", exc)
            sim_result, tool = _empty_result(rend.tb_dir), "none"
            stages["simulate"] = "fail"
            failures.append(str(exc))

        # Stage 5: collect coverage (with potential feedback loop)
        combined_C = 0.0
        cov_result: dict = {}
        functional_coverage: dict = {
            "covered_bins": 0, "valid_bins": 0, "functional_coverage_pct": 0.0,
            "per_coverpoint": [], "bin_summary": {"total": 0, "covered": 0, "uncovered": 0},
        }

        if stages["simulate"] == "ok":
            line_h, line_t, br_h, br_t = _read_line_branch(sim_result, tool)
            raw_func = parse_cocotb_json(sim_result.functional_cov) if sim_result.functional_cov else {}
            functional_coverage = reconcile_with_bins(raw_func, bins)
            cov_result = compute_combined(
                line_h, line_t, br_h, br_t,
                functional_coverage["covered_bins"], functional_coverage["valid_bins"],
            )
            combined_C = cov_result["combined_C"]
            stages["coverage_collect"] = "ok"

            # Coverage-feedback loop: bump weights for uncovered bins and re-simulate.
            for it in range(1, MAX_ITER + 1):
                if combined_C >= TARGET_C:
                    break
                log.info("Coverage %.2f%% < 85%% target, entering feedback iter %d", combined_C, it)
                feedback_adjust(constraints, bins, functional_coverage, iteration=it)
                rend = render_tb(design, constraints, bins, out)
                try:
                    sim_result, tool = run_simulation(rend.tb_dir, seed=seed, num_seq=num_seq, timeout_sec=timeout_sec)
                    stages["simulate"] = "ok"
                except Exception as exc:
                    log.warning("feedback iter %d simulator failed: %s", it, exc)
                    failures.append(f"feedback iter {it} simulator: {exc}")
                    break

                line_h, line_t, br_h, br_t = _read_line_branch(sim_result, tool)
                raw_func = parse_cocotb_json(sim_result.functional_cov) if sim_result.functional_cov else {}
                functional_coverage = reconcile_with_bins(raw_func, bins)
                cov_result = compute_combined(
                    line_h, line_t, br_h, br_t,
                    functional_coverage["covered_bins"], functional_coverage["valid_bins"],
                )
                combined_C = cov_result["combined_C"]
        else:
            stages["coverage_collect"] = "fail"
            cov_result = compute_combined(0, 0, 0, 0, 0, 0)

        # Stage 6: write outputs
        write_all(ReporterInputs(
            design=design.to_dict(),
            skeleton=rend.skeleton,
            constraints=constraints,
            bins=bins,
            functional=functional_coverage,
            cov_result=cov_result,
            stages=stages,
            reproducible_command=_reproducible_cmd(rtl_dir, top, out, seed, num_seq),
            tool=tool,
            failures=_scrape_failures(rend.tb_dir),
            out_dir=out,
        ))

        return PipelineResult(ok=True, stages=stages, cov_combined=combined_C)
    except Exception as exc:
        log.exception("pipeline error")
        stages = _mark_failed_stage(stages, _stage_of_error(exc))
        # Write partial output so the user can debug
        try:
            write_all(ReporterInputs(
                design={"top": top, "error": str(exc), "rtl_files": []},
                skeleton={"drivers": [], "monitors": [], "scoreboard": {}, "testbench_source": ""},
                constraints=constraints if 'constraints' in locals() else {"seed": seed, "num_seq": num_seq},
                bins=bins if 'bins' in locals() else {"coverpoints": []},
                functional=functional_coverage if 'functional_coverage' in locals() else {"covered_bins": 0, "valid_bins": 0},
                cov_result=cov_result if 'cov_result' in locals() else {"line": 0, "branch": 0, "functional": 0, "combined_C": 0},
                stages=stages,
                reproducible_command=_reproducible_cmd(rtl_dir, top, out, seed, num_seq),
                tool="none",
                failures=[str(exc)],
                out_dir=out,
            ))
        except Exception:
            log.exception("failed to write partial report")
        return PipelineResult(ok=False, error=str(exc), stages=stages)


def _read_line_branch(sim_result, tool):
    if sim_result.coverage_xml and Path(sim_result.coverage_xml).exists():
        return parse_verilator_xml(sim_result.coverage_xml)
    if sim_result.coverage_dat and Path(sim_result.coverage_dat).exists():
        return parse_icarus_dat(sim_result.coverage_dat)
    return 0, 0, 0, 0


def _empty_result(tb_dir):
    from .sim.base import RunResult
    return RunResult(
        exit_code=1,
        stdout_path=Path(tb_dir) / "skipped.stdout",
        stderr_path=Path(tb_dir) / "skipped.stderr",
    )


def _scrape_failures(tb_dir) -> list[str]:
    """Pull first ~50 mismatch / failure messages from the sim run log."""
    log_path = Path(tb_dir) / "sim_run.log"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    lines = [line for line in text.split("\n") if any(tag in line for tag in (
        "MISMATCH", "ERROR", "FAIL", "scoreboard"
    ))]
    return lines[:50]


def _reproducible_cmd(rtl_dir, top, out_dir, seed, num_seq) -> str:
    return (
        f"./run.sh --rtl {rtl_dir} --top {top} --out {out_dir} "
        f"--seed {seed} --num-seq {num_seq}"
    )


def _stage_of_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "parse" in msg or "rtl" in msg:
        return "parse"
    if "skeleton" in msg or "render" in msg or "tb_gen" in msg:
        return "skeleton_gen"
    if "simulate" in msg or "verilator" in msg or "iverilog" in msg:
        return "simulate"
    if "coverage" in msg:
        return "coverage_collect"
    return "parse"


def _mark_failed_stage(stages, key):
    for k in stages:
        if stages[k] == "pending":
            stages[k] = "fail"
            break
    else:
        stages[key] = "fail"
    return stages
