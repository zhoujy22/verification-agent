"""Pytest configuration. Provides shared fixtures for RTL parser / pipeline tests."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def tmp_case_dir(tmp_path: Path) -> Path:
    """Temp directory with the standard submission_out layout."""
    (tmp_path / "rtl").mkdir()
    (tmp_path / "submission_out").mkdir()
    return tmp_path


@pytest.fixture
def wrote_rtl(tmp_case_dir: Path):
    """Helper that drops a Verilog file into tmp_case_dir/rtl and returns its full path."""
    def _write(name: str, source: str) -> Path:
        path = tmp_case_dir / "rtl" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path
    return _write


@pytest.fixture(scope="session")
def verilator_available() -> bool:
    return shutil.which("verilator") is not None


@pytest.fixture(scope="session")
def iverilog_available() -> bool:
    return shutil.which("iverilog") is not None or shutil.which("vvp") is not None
