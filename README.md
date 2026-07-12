# verif_agent — RTL 验证环境自动生成 Agent

赛事 A2（"面向 RTL 的 Agent 自动验证环境生成"）的参赛实现。

给定 RTL（Verilog），自动解析接口 → 推断协议 → 生成 cocotb testbench → 仿真 → 收集 3 类覆盖率 → 按 spec 规定的 7 个 JSON 输出报告。

## 文件清单

```
verif_agent/                 # 主包（CLI、pipeline、parser、生成器等）
├── __main__.py              # python -m verif_agent
├── cli.py                   # argparse CLI
├── pipeline.py              # 总装
├── design.py                # Port / Clock / Reset / Design 数据类
├── classifier.py            # 协议识别
├── constraints_gen.py       # constraints.json 生成
├── coverage_definer.py      # coverage_bins.json 生成（bin 必有 sampling_condition）
├── feedback.py              # 覆盖率反馈迭代（max 2）
├── reporter.py              # 7 个 JSON 输出
├── rtl_parser/              # preprocess / regex / pyverilog / port_resolver
├── tb_gen/                  # 时钟复位 + 渲染 + 4 个协议生成器
├── sim/                     # Verilator / Icarus runner + 自动 fallback
├── coverage/                # line_branch / functional / aggregator
└── schemas/                 # JSON Schema 自检（spec 7 条硬约束）

tests/
├── test_parser.py
├── test_classifier.py
├── test_constraints_and_bins.py
├── test_protocols.py
├── test_schemas.py
├── test_case1_smoke.py
├── test_reproducibility.py
├── test_fallback.py
└── test_bin_validity.py

public_dataset/case1/        # 最小流式 DUT 样例
├── rtl/stream_dut.v
└── README.md

run.sh                       # 薄壳，转发到 python3 run.py
run.py                       # 直接 python 入口
Dockerfile                   # python:3.11.9-slim-bookworm
requirements.txt             # 锁版本依赖
```

## 用法

### Docker 内（推荐评测环境）

```bash
docker build -t verif-agent:0.1.0 .
docker run --rm -v "$PWD:/work" verif-agent:0.1.0 \
  --rtl benchmark/rtl --top dut --out submission_out/case_name \
  --seed 12345 --num-seq 5000
```

### 本地

```bash
pip install -r requirements.txt
./run.sh --rtl public_dataset/case1/rtl --top stream_dut \
         --out /tmp/case1 --seed 1 --num-seq 5000
# 或：
python3 run.py --rtl public_dataset/case1/rtl --top stream_dut \
               --out /tmp/case1 --seed 1 --num-seq 5000
```

## 产物布局（每个 case 写到 `--out`）

```
<out>/
├── design.json                    # 接口解析结果（top, rtl_files, ports, clock/reset, protocols）
├── verification_skeleton.json     # 验证骨架（drivers / monitors / scoreboard / tb source）
├── constraints.json               # 约束随机测试策略（seed, num_seq, random_vars, feedback updates）
├── coverage_bins.json             # 功能覆盖率目标（coverpoints/bins，bin 必有 sampling_condition）
├── functional_coverage.json       # 采样结果（covered_bins, valid_bins, per-coverpoint）
├── coverage_result.json           # line / branch / functional / combined_C
├── report.json                    # 顶层汇总（4 stage 标志 + reproducible_command + environment）
├── generated_tb/                  # tb_top.py / dut_inst.v / Makefile / coverpoints.py / sim_run.log
└── generated_tests/               # reserved
```

## 评分映射（spec §65-）

- **骨架分 3 分（门禁项）** — 由 verification_skeleton.json 体现。drivers/monitors/scoreboard 必须非空且非壳。
- **覆盖率分 7 分** — C = 0.4·line + 0.3·branch + 0.3·functional，按阈值：
  - C ≥ 85% → 7.0
  - 65–85% → 4.9
  - 45–65% → 2.8
  - < 45% → 0
- 每电路 10 分 × 10 = 100 分。

## 关键技术决策

- **仿真器**：默认 Verilator（cocotb Makefile，`SIM=verilator`），Verilator 拒绝的 RTL 自动切到 Icarus Verilog（`SIM=icarus`）。verilator 专用 flag（`--coverage --build -Wno-fatal`）在生成 Makefile 里用 `ifeq ($(SIM),verilator)` 包起来，不会漏给 iverilog 导致 `invalid option`。
- **Testbench**：cocotb + cocotb-coverage（驱动/监视/记分板 + 功能 bin 命中采样）。
- **RTL 解析**：PyVerilog 主路径（惰性导入）+ 正则兜底（保证 ports 列表在 PyVerilog 失败或未安装时仍非空）。
- **唯一 RNG 实例**：`random.Random(int(args.seed))`，保证同 seed 输出字节相同。

## 测试

```bash
pip install -r requirements.txt
python -m pytest tests/test_parser.py -q
python -m pytest tests/test_classifier.py -q
python -m pytest tests/test_constraints_and_bins.py -q
python -m pytest tests/test_protocols.py -q
python -m pytest tests/test_schemas.py -q
python -m pytest tests/test_case1_smoke.py -q   # 需要 verilator/iverilog
python -m pytest tests/test_reproducibility.py -q
python -m pytest tests/test_fallback.py -q
python -m pytest tests/test_bin_validity.py -q
```

## TODO（拿到赛方 `public_dataset/case1..case5` 后）

- 新增 `verif_agent/field_mapping.py` 适配层（当前尚未创建）
- 用官方 `expected/*.json` 作为对齐基准，重命名 key（如 `top` → `top_module`）
- lock 版本签名跑通 → `diff` 通过
