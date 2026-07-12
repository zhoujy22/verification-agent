"""verif_agent — RTL 自动验证环境生成 Agent.

Per spec docs/A2_验证环境自动生成.pdf:
- Parse RTL → infer protocol → generate cocotb testbench → simulate → collect coverage.
- Output 7 fixed JSON files under submission_out/<case_name>/.
- Score: 10 hidden circuits × 10 pts; gate (3 pts skeleton) + 7 pts coverage (C >= 85% full).
"""
__version__ = "0.1.0"
