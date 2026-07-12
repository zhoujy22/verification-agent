"""Direct Python entry point. Usage:
    python3 run.py --rtl benchmark/rtl --top dut --out submission_out/case_name --seed 12345 --num-seq 5000
"""
from verif_agent.cli import main

if __name__ == "__main__":
    main()
