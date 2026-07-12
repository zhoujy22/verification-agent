"""Common dataclass for simulator run results."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RunResult:
    """Outcome of one simulator invocation."""
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    coverage_dat: Optional[Path] = None
    coverage_xml: Optional[Path] = None
    functional_cov: Optional[Path] = None
    log_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
