"""Smoke test: CLI parser accepts the spec-mandated args form."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_help():
    proc = subprocess.run(
        [sys.executable, "-m", "verif_agent", "--help"],
        capture_output=True, text=True, cwd=REPO_ROOT,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "--rtl" in proc.stdout
    assert "--top" in proc.stdout
    assert "--out" in proc.stdout
    assert "--seed" in proc.stdout
    assert "--num-seq" in proc.stdout


def test_cli_missing_required_args():
    proc = subprocess.run(
        [sys.executable, "-m", "verif_agent"],
        capture_output=True, text=True, cwd=REPO_ROOT,
        timeout=15,
    )
    assert proc.returncode != 0
    assert "required" in proc.stderr or "error" in proc.stderr.lower()
