# verif_agent — RTL 验证环境自动生成

给定 RTL（Verilog），自动生成 cocotb testbench 并输出 7 个 JSON 报告。支持 AXI / AXI-Lite / AXI-Stream / SRAM / valid-ready stream 接口。

---

## 一、快速开始

### 方式 A：pip 安装（Linux）

```bash
pip install -r requirements.txt

./run.sh --rtl <RTL目录> --top <顶层模块名> --out <输出目录> --seed 12345 --num-seq 5000
```

示例：

```bash
./run.sh --rtl public/A2-verification/testcases/A2_public_dataset/case1/rtl \
         --top axi_adapter_rd --out out/case1 --seed 12345 --num-seq 5000
```

需要 Python 3.11+，以及 `verilator` 或 `iverilog`（agent 自检仿真用，可选）。

### 方式 B：Docker（Linux）

```bash
docker build -t verif-agent:0.1.0 .

MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/work" verif-agent:0.1.0 \
  --rtl <RTL目录> --top <顶层模块名> --out <输出目录> --seed 12345 --num-seq 5000
```

---

## 二、产出物

```
<out>/
├── design.json                    # 接口解析（端口、时钟复位、接口分组）
├── verification_skeleton.json     # 验证骨架（driver/monitor/scoreboard，与 tb 一致）
├── constraints.json               # 约束随机测试策略
├── coverage_bins.json             # 功能覆盖率 bin 定义
├── functional_coverage.json       # 功能 bin 采样结果
├── coverage_result.json           # 行/分支/功能/综合覆盖率（agent 自检值）
├── report.json                    # 阶段汇总 + 可复现命令
└── generated/                     # 交付用 testbench（自包含）
    ├── rtl/                        #   RTL 源文件副本
    └── tb/                         #   cocotb testbench
        ├── tb_top.py               #     测试主程序
        ├── dut_inst.v              #     DUT 例化 wrapper
        ├── Makefile                #     cocotb 仿真 Makefile
        └── coverpoints.py
```

`generated/` 自包含（RTL 已复制进去，路径全相对），拷走即用，不依赖原 RTL 位置。

---

## 三、VCS 评测

### 1. 装依赖（赛方机器首次需要一次）

```bash
pip install cocotb==1.9.0 cocotbext-axi==0.1.28 cocotb-coverage==1.1.0
```

需要 Python 3.8+（推荐 3.11）。若 cocotb-config 不在 PATH：`export PATH="$HOME/.local/bin:$PATH"`。

### 2. 用 VCS 跑 testbench

```bash
cd <out>/generated/tb

make SIM=vcs \
     MODULE=tb_top TESTCASE=run_main \
     COMPILE_ARGS="-cm line+branch -cm_dir sim_build/coverage.vdb" \
     SIM_ARGS="-cm_dir sim_build/coverage.vdb" \
     SEED=12345 NUM_SEQ=5000
```

### 3. 采集覆盖率

```bash
urg -dir sim_build/coverage.vdb -report sim_build/urg_report
```

覆盖率以 VCS/URG 采集为准（`coverage_result.json` 里的数字是 agent 在 Verilator 下的自检值，仅供参考）。功能覆盖率由 testbench 自身采样，见 `functional_coverage.json`。

综合覆盖率：`C = 0.4×行 + 0.3×分支 + 0.3×功能`。
