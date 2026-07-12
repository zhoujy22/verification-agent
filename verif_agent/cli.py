"""CLI entry point. Mirrors spec §46-48:
    ./run.sh  --rtl benchmark/rtl --top dut --out submission_out/case_name --seed x --num-seq 5000
    python3 run.py [same args]
"""
from __future__ import annotations

import argparse
import logging
import sys

from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verif_agent",
        description="RTL 自动验证环境生成 Agent",
    )
    p.add_argument("--rtl", required=True, help="RTL 输入目录 (含 .v/.sv/.vh)")
    p.add_argument("--top", required=True, help="顶层模块名")
    p.add_argument("--out", required=True, help="产物输出目录")
    p.add_argument("--seed", type=int, default=12345, help="固定 seed (default 12345)")
    p.add_argument("--num-seq", type=int, default=5000, help="序列数量 (default 5000)")
    p.add_argument("--timeout", type=int, default=600, help="单次仿真超时秒数")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = run(
        rtl_dir=args.rtl,
        top=args.top,
        out_dir=args.out,
        seed=args.seed,
        num_seq=args.num_seq,
        timeout_sec=args.timeout,
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
