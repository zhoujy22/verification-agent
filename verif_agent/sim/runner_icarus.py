"""Icarus Verilog fallback runner.

Used when Verilator rejects the RTL (e.g. `specify` blocks, real-valued regs).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import RunResult


class IcarusFailed(Exception):
    """Icarus build/run failed; surface message."""


class IcarusRunner:
    """Drive cocotb with Icarus Verilog (SIM=icarus)."""

    def run(self, tb_dir, seed: int, num_seq: int, timeout_sec: int = 600) -> RunResult:
        tb_dir = Path(tb_dir)
        if not (tb_dir / "Makefile").exists():
            raise IcarusFailed(f"No Makefile in {tb_dir}")
        if shutil.which("iverilog") is None and shutil.which("vvp") is None:
            raise IcarusFailed("iverilog not installed")

        stdout_path = tb_dir / "icarus_run.stdout"
        stderr_path = tb_dir / "icarus_run.stderr"

        cmd = [
            "make",
            "-C", str(tb_dir),
            "SIM=icarus",
            f"SEED={seed}",
            f"NUM_SEQ={num_seq}",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env={**os.environ, "SIM": "icarus"},
            )
        except subprocess.TimeoutExpired as exc:
            raise IcarusFailed(f"icarus timed out after {timeout_sec}s") from exc

        stdout_path.write_text(proc.stdout or "", encoding="utf-8", errors="ignore")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8", errors="ignore")

        coverage_dat = tb_dir / "coverage.dat"
        coverage_xml = tb_dir / "coverage.xml"
        functional_cov = tb_dir / "functional_cov.json"

        if proc.returncode != 0:
            raise IcarusFailed(
                f"icarus make exited {proc.returncode}; tail:\n"
                f"{(proc.stderr or '')[-1000:]}"
            )

        return RunResult(
            exit_code=proc.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            coverage_dat=coverage_dat if coverage_dat.exists() else None,
            coverage_xml=coverage_xml if coverage_xml.exists() else None,
            functional_cov=functional_cov if functional_cov.exists() else None,
            log_tail=(proc.stdout or "")[-1500:],
        )
