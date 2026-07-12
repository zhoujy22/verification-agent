# case1 — 最小冒烟用例

这是一个 16-bit (default) valid/ready 流式 DUT，用于验证整条 pipeline 的形态正确性，
并不代表隐藏评测的真实复杂度。

## 文件
- `rtl/stream_dut.v` — 2-deep skid buffer，参数 WIDTH=8（冒烟更易观察）

## 跑法

```bash
cd /path/to/tb_enviroment
./run.sh --rtl public_dataset/case1/rtl --top stream_dut \
         --out /tmp/case1 --seed 1 --num-seq 5000
```

## 预期产物 (`/tmp/case1/`)
- `design.json` — `primary_protocol == "valid_ready_stream"`
- `verification_skeleton.json` — `drivers` 与 `monitors` 非空
- `constraints.json` — `seed=1, num_seq=5000`
- `coverage_bins.json` — cp_payload / cp_backpressure / cp_idle
- `functional_coverage.json` — covered bins/valid bins 数值
- `coverage_result.json` — `combined_C` 在 stream 这种简单 DUT 上预期 ≥ 30
- `report.json` — 4 个 stage 标志 + 可复现命令
- `generated_tb/` — tb_top.py / dut_inst.v / Makefile / coverpoints.py
- `generated_tests/`

## 注意事项
- 本样例 RTL 故意保持极简；隐藏评测可能含多文件、include、`specify` 块、burst 等。
- 若本地缺 `verilator` / `iverilog`，pipeline 会在 `--out` 写出错误日志但仍产出 7 个 JSON。
- `expected/` 目录保留为空 — 由本项目 `reporter.write_all()` 自动产出权威 JSON。
