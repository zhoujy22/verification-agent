"""Simulator runner package.

Two concrete runners (Verilator preferred, Icarus fallback) plus a VCS stub.
The pipeline calls run_simulation() which handles fallback transparently.
"""
from .runner_verilator import VerilatorRunner, VerilatorFailed
from .runner_icarus import IcarusRunner, IcarusFailed


def run_simulation(tb_dir, seed, num_seq, timeout_sec=600):
    """Try Verilator first; on failure, fall back to Icarus. Returns (RunResult, tool_str)."""
    try:
        result = VerilatorRunner().run(tb_dir, seed, num_seq, timeout_sec=timeout_sec)
        return result, "verilator"
    except VerilatorFailed:
        result = IcarusRunner().run(tb_dir, seed, num_seq, timeout_sec=timeout_sec)
        return result, "iverilog"
