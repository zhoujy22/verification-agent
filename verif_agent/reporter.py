"""Write all 7 required JSON outputs to submission_out/<case>/.

Per spec:
  - design.json
  - verification_skeleton.json
  - constraints.json
  - coverage_bins.json
  - functional_coverage.json
  - coverage_result.json
  - report.json
"""
from __future__ import annotations

import json
import logging
import platform
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


REQUIRED_FILES = [
    "design.json",
    "verification_skeleton.json",
    "constraints.json",
    "coverage_bins.json",
    "functional_coverage.json",
    "coverage_result.json",
    "report.json",
]


@dataclass
class ReporterInputs:
    design: dict
    skeleton: dict
    constraints: dict
    bins: dict
    functional: dict
    cov_result: dict
    stages: dict
    reproducible_command: str
    tool: str
    failures: list[str]
    out_dir: Path


def write_all(inputs: ReporterInputs) -> None:
    out_dir = Path(inputs.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "design.json").write_text(_pretty(inputs.design), encoding="utf-8")
    (out_dir / "verification_skeleton.json").write_text(_pretty(inputs.skeleton), encoding="utf-8")
    (out_dir / "constraints.json").write_text(_pretty(inputs.constraints), encoding="utf-8")
    (out_dir / "coverage_bins.json").write_text(_pretty(inputs.bins), encoding="utf-8")
    (out_dir / "functional_coverage.json").write_text(_pretty(inputs.functional), encoding="utf-8")
    (out_dir / "coverage_result.json").write_text(_pretty(inputs.cov_result), encoding="utf-8")

    out_paths = {f: str((out_dir / f).resolve()) for f in REQUIRED_FILES[:6]}
    out_paths["generated_tb_dir"] = str((out_dir / "generated_tb").resolve())
    out_paths["generated_tests_dir"] = str((out_dir / "generated_tests").resolve())

    report = {
        "schema_version": 1,
        "case_name": inputs.design.get("case_name", inputs.design.get("top", "")),
        "stages": inputs.stages,
        "outputs": out_paths,
        "coverage_summary": {
            "line": inputs.cov_result.get("line"),
            "branch": inputs.cov_result.get("branch"),
            "functional": inputs.cov_result.get("functional"),
            "combined_C": inputs.cov_result.get("combined_C"),
        },
        "reproducible_command": inputs.reproducible_command,
        "environment": {
            "python": platform.python_version(),
            "tool": inputs.tool,
            "docker_image": "verif-agent:0.1.0",
            "platform": platform.platform(),
        },
        "failures": inputs.failures[:50],
    }
    (out_dir / "report.json").write_text(_pretty(report), encoding="utf-8")


def assert_all_outputs_present(out_dir: Path) -> tuple[bool, list[str]]:
    missing = []
    for f in REQUIRED_FILES:
        if not (Path(out_dir) / f).exists():
            missing.append(f)
    return (not missing), missing


def _pretty(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)
