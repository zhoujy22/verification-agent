# 实施方案与技术细节

> 本文档面向工程实施者 —— 解释 **每个算法在做什么**、**代码在哪、怎么调**。  
> 上游文档：[`docs/A2_验证环境自动生成.pdf`](A2_验证环境自动生成.pdf)（赛题）、  
> 计划文档：[`replicated-churning-forest.md`](../../AppData/Roaming/claude-code-plans/...)（已批准的方案）。

---

## 0. 总览（30 秒读懂）

本系统把 **给定 RTL** 转换成 **7 个 JSON + 完整 cocotb testbench**，按 spec 规定的目录写到 `submission_out/<case>/`。

**单次调用**：
```bash
./run.sh --rtl <RTL_DIR> --top <MODULE> --out <OUT_DIR> --seed N --num-seq 5000
```

**8 个 stage**（pipeline.py 里串成一条线，任一阶段失败都尝试写出部分产物）：

```
parse → classify → coverpoints/constraints → render_tb
       → simulate (Verilator → Icarus fallback) → collect_coverage
       → feedback (max 2 iter) → write_all
```

每次失败都在 `report.json.stages.<stage>` 里记 `ok/fail`，**任何阶段失败都会写完所有 7 个 JSON**（方便评测方定位）。

---

## 1. 仓库结构

```
D:/111/tb_enviroment/
├── run.sh / run.py / Dockerfile / requirements.txt / README.md
├── verif_agent/                       # 主包（详细目录见 §2.1）
├── public_dataset/case1/              # 最小流式 DUT 样例
├── tests/                             # pytest 测试套件（55 tests）
└── docs/
    ├── A2_验证环境自动生成.pdf        # 赛题原文
    └── ALGORITHMS.md                  # 本文档
```

### 1.1 `verif_agent/` 内部结构

```
verif_agent/
├── __main__.py / cli.py / pipeline.py        # CLI、orchestrator
├── design.py                                 # @dataclass 数据类
├── classifier.py                             # 协议识别（§4）
├── constraints_gen.py                        # constraints.json 生成（§5.1）
├── coverage_definer.py                       # coverage_bins.json 生成（§5.2）
├── feedback.py                               # 反馈迭代（§7）
├── reporter.py                               # 7 个 JSON 写出（§8）
│
├── rtl_parser/                               # RTL 解析（§3）
│   ├── preprocess.py                         # 注释/include/ifdef 清理
│   ├── regex_parser.py                       # 兜底正则
│   ├── pyverilog_parser.py                   # 主路径
│   └── port_resolver.py                      # 两路合并 + 参数表达式解析
│
├── tb_gen/                                   # testbench 生成（§6）
│   ├── clock_reset.py                        # 时钟/复位协程模板
│   ├── render.py                             # 渲染 tb_top.py 等 5 个文件 + 骨架
│   └── protocols/
│       ├── base.py                           # ProtocolOutput dataclass
│       ├── axi_lite.py / axi_full.py         # AXI4 / AXI4-Lite
│       ├── sram.py                           # SRAM-like
│       ├── stream.py                         # valid/ready 流
│       ├── apb.py                            # APB（Plan B）
│       └── generic.py                        # 兜底随机 toggle（Plan A）
│
├── sim/                                      # 仿真执行（§8）
│   ├── base.py / runner_verilator.py / runner_icarus.py
│
├── coverage/                                 # 覆盖率收集（§9）
│   ├── line_branch.py                        # Verilator coverage.xml
│   ├── functional.py                         # cocotb functional_cov.json
│   └── aggregator.py                         # C = 0.4·L + 0.3·B + 0.3·F
│
└── schemas/                                  # JSON Schema 自检（spec §7 条硬约束）
```

---

## 2. 数据模型（`verif_agent/design.py`）

```python
@dataclass
class Port:
    name: str                                  # 端口名
    direction: "input" | "output" | "inout"
    width: int                                 # 1 表示 scalar
    sign: "unsigned" | "signed"
    protocol_group: str                        # "clk" | "rst" | "axi_aw" | "sram" | ...
    role: "clk" | "rst" | "driver" | "monitor" | "passive"

@dataclass
class Parameter: name, value, width, signed
@dataclass
class Clock:    name, period_ns=10
@dataclass
class Reset:    name, active_level ∈ {0,1}, duration_cycles=5

@dataclass
class Design:
    top: str
    rtl_files: list[str]
    compile_order: list[str]                   # 经 include 拓扑排序
    clock: list[Clock];  reset: list[Reset]
    parameters: list[Parameter]
    ports: list[Port]
    inferred_protocols: list[str]              # ["AXI4-Lite", "SRAM", ...]
    primary_protocol: str                      # 第一位（排序：AXI > APB > SRAM > stream）
```

**`Design` 是模块之间的唯一载体**，从 `rtl_parser.resolve` 一路传到 `reporter.write_all`。

---

## 3. Stage 1: RTL 解析（`rtl_parser/`）

### 3.1 算法目标

给定 `rtl_dir`（可能含多个 `.v/.sv/.vh/.svh` 文件），产生 `Design` 对象。

**鲁棒性是核心**（spec §34 间接：解析失败 → 0.8 分拿不到 → 后续结构崩塌）。

### 3.2 算法：双路解析 + 合并

```
每个 RTL 文件 ──▶ preprocess()
                │
                ├──▶ regex_parser.parse()        ← 兜底，始终产出 port 列表
                │
                └──▶ pyverilog_parser.parse()    ← 主路径，增强 type/signedness
                     ↓ 失败时抛 ParseError
                     仅用 regex 的结果
                │
                ▼
        port_resolver.merge()
                │
                ├── PyVerilog 端口合并到 regex 端口（PyVerilog 类型/signedness 优先）
                ├── 参数宽度解析（如 `ADDR_WIDTH-1:0` → 32）
                ├── 时钟/复位信号识别（端口名启发式）
                └── include 拓扑排序得到 compile_order
```

### 3.3 关键算法

**A. 注释/include 处理** — `rtl_parser/preprocess.py:preprocess()`
```
输入：原始 Verilog 源
步骤：
  1. 去掉 /* ... */ 块注释
  2. 去掉 // 行注释
  3. 合并 `\` 行继续符
  4. `include "foo.vh" → 读 include_dirs 里的 foo.vh（带去重防止循环）
  5. ifdef 白名单（{SIMULATION, VERILATOR, COCOTB_SIM, DEBUG, SYNTHESIS}）简单保留
```

**B. 正则端口提取** — `rtl_parser/regex_parser.py:parse()`
同时支持 ANSI 与非 ANSI 风格：

```verilog
// ANSI-style：
module foo #(parameter W=8) (input [W-1:0] p1, output reg p2);
// 非-ANSI：
module foo (p1, p2);
    input [W-1:0] p1;
    output reg p2;
```

`ModuleInfo` dataclass 保留原始 `width_expr`（如 `"ADDR_WIDTH-1:0"`），后面 `port_resolver` 用参数值代入算 width。

**C. 参数化宽度解析** — `rtl_parser/port_resolver.py:_resolve_widths()`
```
输入：rx_items（带 width_expr）、params（已求值）
对每个 port：
  1. 替换 width_expr 里的参数名为参数值（按名字长度从长到短避免子串冲突）
  2. 匹配 `<expr>:<expr>` MSB:LSB 形式 → 高 - 低 + 1
  3. 求值（窄沙箱 eval）
失败兜底：width=1（信号实际更宽时会编译报错）
```

**D. PyVerilog 主路径增强** — `rtl_parser/pyverilog_parser.py:parse()`
- 用 `pyverilog.vparser.parser.parse_verilog()` 解析预处理过的源码
- 提取 module 名字、port 列表（dir/width/signed）、parameter 列表
- 任何异常 → 抛 `ParseError`，由 `port_resolver` 忽略

### 3.4 关键入口与拒绝条件

```python
# rtl_parser/__init__.py
discover_rtl_files(rtl_dir: Path) -> list[str]   # 找出所有 .v/.sv/.vh
resolve(rtl_dir, top: str) -> Design              # 主入口
```

**`resolve` 抛 `RuntimeError`** 当目录无 `.v` 文件。

---

## 4. Stage 2: 协议分类（`classifier.py`）

### 4.1 算法目标

就地修改 `Design.ports`，给每个端口填 `protocol_group` + `role`，并写 `inferred_protocols` / `primary_protocol`。

### 4.2 算法：两遍扫描

**Pass 1 — 全局协议判定**（拿整个 set）

```python
port_names_lower = {p.name.lower() for p in design.ports}
stripped_axi = {_strip_axi_prefix(n) for n in port_names_lower}   # 剥 m_axi_/s_axi_/m0_/...
stripped_apb = {_strip_apb_prefix(n) for n in port_names_lower}

is_axi, variant = _classify_axi(port_names_lower)   # 真假 + AXI4 vs AXI4-Lite
is_apb = _classify_apb(port_names_lower)
is_sram = _classify_sram(port_names_lower)
is_stream = _classify_stream(port_names_lower)
```

判定规则（**先匹配者胜**，spec §5-6 表述的层级）：

```
AXI4    =  {aw, w, b, ar, r} 5 个 channel 全有 + 至少一个 burst 指示信号（awsize/awburst/awlen/awid/awlock 或对应 ar）
AXI4-Lite = AXI4 但无 burst 指示信号（地址 ≤ 32，不需 burst）
APB     =  {psel, penable, pwrite, pready} 4 个核心信号都在（剥前缀后）
SRAM    =  含 csb/cs_n/cen + ≥3 个 we/addr/din/dout/wmask/be
stream  =  {*_valid, *_ready, *_data} 三元组 + 无 AW/W channel
passive =  以上都不满足
```

**算法关键**：
- `_strip_axi_prefix` 剥 `m_axi_/s_axi_/m0_/s0_/axi_` 这些常见前缀再做信号集匹配
- `_strip_apb_prefix` 同理剥 `m_apb_/s_apb_/p_/apb_`
- 这让 `m_axi_awaddr` 这种命名自动识别成 AXI `axi_aw` 组（Plan B）

**优先级 / `inferred_protocols` 顺序**：
```python
protocols.append(axi_variant)   # 1. AXI4 / AXI4-Lite
protocols.append("APB")        # 2. APB
protocols.append("SRAM")       # 3. SRAM
protocols.append("valid_ready_stream")  # 4. stream
design.primary_protocol = protocols[0] if protocols else ""
```

**Pass 2 — 按端口写 group + role**

每个端口走下面这个顺序（**先匹配者胜**）：

```
1. name 匹配 CLOCK_NAMES    → group=clk,    role=clk
2. name 像 reset            → group=rst,    role=rst
3. AXI 已识别 + 剥前缀后是 AXI 信号集 → group=axi_{aw|w|b|ar|r}, role=driver|monitor
4. APB 已识别 + 剥前缀后是 APB 信号集 → group=apb, role 由 _apb_signal_role 决定
5. SRAM 已识别 + name 在 SRAM signals → group=sram, role=driver|monitor
6. stream 已识别 + name 在某 stem → group=stream_in|stream_out, role=driver|monitor
7. 其他 → group=passive, role=passive
```

**stream 的 in/out 判定**：根据端口 `direction` 反向推 — input 端口的 `_valid` 是 DUT 接收 ⇒ stem=in；output 端口的 `_valid` 是 DUT 发送 ⇒ stem=out。

---

## 5. Stage 3: constraints & coverage bins

### 5.1 `constraints_gen.py:generate(design, seed, num_seq=5000)`

**算法**：每个 `role=driver` 的端口产生一条 `random_variable` 条目。

```python
random_vars = []
for p in design.ports:
    if p.role != "driver":
        continue
    var = {
        "name": p.name,
        "width": p.width,
        "range": [0, (1 << p.width) - 1],
        "dist": "uniform",
        "protocol_constraint": "",
        "signal_group": p.protocol_group,
    }
    # AXI 的特殊分布
    if p.name == "wstrb":
        var["dist"] = "weighted"
        var["weights"] = {"0xF": 60, "0x1": 5, ..., "0x0": 12}   # 偏向全 strobe
    if p.name == "awburst":
        var["weights"] = {"INCR": 70, "FIXED": 20, "WRAP": 10}
    ...
    random_vars.append(var)

return {
    "seed": seed, "num_seq": num_seq,
    "random_variables": random_vars,
    "protocol_constraints": [...],
    "coverage_feedback_updates": [],   # feedback.py 后面填
}
```

**关键设计**：
- `weights` 让 wstrb 偏向全 1、awburst 偏向 INCR，覆盖典型 DUT 的 corner case
- `dist` 字段后续可被 feedback 模块修改以提升覆盖率

### 5.2 `coverage_definer.py:define(design)`

**算法**：按 `design.primary_protocol` 调对应 coverpoint 生成器。

```python
if primary == "AXI4" or "AXI4-Lite":
    coverpoints.extend(_axi_bins(design))    # 5 个 cp
if primary == "SRAM":   coverpoints.extend(_sram_bins(design))    # 3 个 cp
if primary == "valid_ready_stream": coverpoints.extend(_stream_bins(design))  # 3 个 cp
if primary == "APB":    coverpoints.extend(_apb_bins(design))     # 4 个 cp
if not coverpoints:
    coverpoints.extend(_generic_bins(design))  # 兜底：1 个 cp_generic_run
```

**每条 bin 都强制要 `sampling_condition`**（spec §34 硬规则）：

```python
def _enforce_sampling_condition(bin_entry: dict) -> dict:
    if not bin_entry.get("sampling_condition"):
        raise ValueError(f"...无 sampling_condition 不计入分母...")
    # 自动补 scenario / covered / hit_count
```

**协议覆盖的 bin（截至当前）**：

| 协议 | coverpoints | bin 名 |
|---|---|---|
| AXI4 / AXI4-Lite | cp_awburst / cp_awsize / cp_wstrb_pattern / cp_resp / cp_align | BIN_FIXED, BIN_INCR, BIN_FULL, BIN_PARTIAL, BIN_ALIGNED, ... |
| SRAM | cp_addr_align / cp_we / cp_wmask | BIN_READ, BIN_WRITE, BIN_FULL ... |
| stream | cp_payload / cp_backpressure / cp_idle | BIN_ZERO, BIN_MAX, BIN_MIX, BIN_NO_BP ... |
| APB | cp_pwrite / cp_penable / cp_addr_align / cp_pready | BIN_WRITE, BIN_READ, BIN_SETUP, BIN_ACCESS ... |
| generic | cp_generic_run | BIN_TICK |

---

## 6. Stage 4: Testbench 生成（`tb_gen/`）

### 6.1 算法：模板拼装

`tb_gen/render.py:render(design, constraints, bins, out_dir)` 调用每个协议生成器拿源码字符串，**再按固定 f-string 模板拼成完整的 `tb_top.py`**。

```
ProtocolOutput            ← 每个 .py 返回
├─ driver_py:    str       ←  async def <driver_name>(dut, rng, num_seq):
├─ monitor_py:   str       ←  async def <monitor_name>(dut, in_q, out_q, ...):
├─ scoreboard_py: str      ←  class <scoreboard_name>:
├─ coverpoint_py: str      ←  def _sample_*_bins(dut, cp_registry):
├─ driver_name / monitor_name / scoreboard_name: str  ← 用作 verification_skeleton 声明
└─ scoreboard_checks: list[str]
```

`PROTOCOL_REGISTRY`（`tb_gen/protocols/__init__.py`）：
```python
{
    "AXI4":         axi_full.generate,
    "AXI4-Lite":    axi_lite.generate,
    "SRAM":         sram.generate,
    "valid_ready_stream": stream.generate,
    "APB":          apb.generate,
    "":             generic.generate,        # 兜底
    "passive":      generic.generate,
}
```

### 6.2 `render.py:_tb_top_py()` — 模板骨架

7 段按固定顺序拼成一个 f-string：

```
A.  import 块 + 单一 RNG
B.  class Hit:  hit = 0
C.  cp_registry 字典（从 bins 自动生成）
D.  async def setup_clock_reset(dut)        ← clock_reset.render()
E.  协议 4 段源码                           ← protocols/<name>.py
F.  async def _dump_functional_cov()        ← 测完 dump
G.  @cocotb.test() async def run_main(dut)  ← cocotb_test_body()
```

**`cocotb_test_body()`** 按协议返回对应的 `@cocotb.test()` 入口：
```python
@cocotb.test()
async def run_main(dut):
    await setup_clock_reset(dut)
    # 起 monitor
    cocotb.start_soon(<monitor_name>(dut, ...))
    sb = <scoreboard_name>(...)
    await <driver_name>(dut, RNG, NUM_SEQ)   # 5000 序列
    await Timer(200, units="ns")
    passed = sb.check()
    await _dump_functional_cov(dut, cp_registry, ...)
    assert passed, sb.mismatch_log[:10]
```

### 6.3 `render.py:_verilog_wrapper()` — Verilog wrapper

```verilog
module tb_top(port_list);
    input/output wire [W-1:0] port;
    ...
    <design.top> dut_inst (.p(p), ...);   ← 按名连
endmodule
```

`dut_inst.v` 自动按 `design.ports` 写完整端口列表 + 实例化。**任何漏连、错连、宽度不匹配都在 verilator 编译时报错**。

### 6.4 `render.py:render()` — 输出与元数据

返回 `RenderResult(tb_dir, skeleton)`：

```python
skeleton = {
    "clock_reset": {...},
    "drivers":  [{"name": proto.driver_name, "interface": ..., ...}],   # 显式声明的
    "monitors": [{"name": proto.monitor_name, ...}],
    "scoreboard": {
        "name": proto.scoreboard_name,
        "type": "transaction_level",
        "checks": proto.scoreboard_checks,                             # 协议相关的检查名
    },
    "dut_wiring": {...},
    "testbench_source": "generated_tb/tb_top.py",
}
```

**关键修复**：所有 `name` / `checks` 都来自协议生成器的显式声明，**不再编字符串**，跟 `tb_top.py` 里的类/函数定义**一字不差**（保证 `verification_skeleton.json` 跟实际代码一致）。

---

## 7. Stage 5: 仿真执行（`sim/`）

### 7.1 算法：Verilator → Icarus fallback

```python
# verif_agent/sim/__init__.py
def run_simulation(tb_dir, seed, num_seq, timeout_sec=600):
    try:
        result = VerilatorRunner().run(tb_dir, seed, num_seq, timeout_sec)
        return result, "verilator"
    except VerilatorFailed:
        result = IcarusRunner().run(tb_dir, seed, num_seq, timeout_sec)
        return result, "iverilog"
```

**两个 runner 接口完全一致**（`SimulatorRunner` ABC），fallback 对调用方透明。

### 7.2 `runner_verilator.py`

```python
class VerilatorRunner:
    def run(self, tb_dir, seed, num_seq, timeout_sec) -> RunResult:
        cmd = ["make", "-C", str(tb_dir), f"SEED={seed}", f"NUM_SEQ={num_seq}"]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
        # cocotb 驱动 Makefile → verilator --build --coverage
        if proc.returncode != 0:
            raise VerilatorFailed(...)     # 触发 Icarus fallback
        return RunResult(
            exit_code, stdout_path, stderr_path,
            coverage_dat, coverage_xml, functional_cov,
        )
```

### 7.3 `runner_icarus.py`

同接口，invoke `make SIM=icarus`。

---

## 8. Stage 6: 覆盖率收集（`coverage/`）

### 8.1 `line_branch.py:parse_verilator_xml(xml)`

```python
for file_node in tree.iter("file"):
    for child in file_node:
        if tag == "line":
            line_total += 1
            if int(count) > 0: line_hits += 1
        elif tag == "branch":
            branch_total += 1
            if int(count) > 0: branch_hits += 1
return (line_hits, line_total, branch_hits, branch_total)
```

Verilator `coverage.xml` 中每个 `<file>` 内有若干 `<line>` 与 `<branch>` 节点，每个带 `count` 属性。`count > 0` 算覆盖到。

### 8.2 `functional.py:parse_cocotb_json(json)` + `reconcile_with_bins(raw, bins_def)`

```python
# Step 1: 读 cocotb 输出的 functional_cov.json
raw = json.loads(path.read_text())
# raw = {"cp_payload": {"BIN_ZERO": {"hit_count": 5, "covered": true}}, ...}

# Step 2: 按 coverage_bins.json 的 cp/bin 名字对齐
for cp in bins_def["coverpoints"]:
    for b in cp["bins"]:
        if not b.get("sampling_condition"):    # 丢弃无采样条件的 bin（spec §34/§116）
            continue
        entry = raw.get(cp.name, {}).get(b.name, {})
        hit = int(entry.get("hit_count", 0))
        valid_bins += 1
        if hit > 0: covered_bins += 1
        # 也写到 per_coverpoint 里

pct = 100.0 * covered / valid
```

**关键 invariant**：`bins_def` 的 cp/bin 名字是权威的；cocotb 输出**必须按此对齐**。

### 8.3 `aggregator.py:compute(...)`

```python
line = 100.0 * line_hits / max(line_total, 1)
branch = 100.0 * branch_hits / max(branch_total, 1)
functional = 100.0 * functional_covered / max(functional_valid, 1)
C = 0.4 * line + 0.3 * branch + 0.3 * functional    # spec §112 公式
return {line, branch, functional, combined_C, ...}
```

按 spec 阈值：
| C | 分 |
|---|---|
| ≥ 85% | 7.0 |
| 65–85 | 4.9 |
| 45–65 | 2.8 |
| < 45 | 0 |

---

## 9. Stage 7: 反馈迭代（`feedback.py`）

```python
MAX_ITER = 2
TARGET_C = 85.0

def adjust(constraints, bins_def, functional, iteration):
    # 加一条 coverage_feedback_updates 记录
    constraints["coverage_feedback_updates"].append({
        "iter": iteration, "trigger": "combined_C<85%", "applied": True,
    })
    # 找出未覆盖 bin，对一些 weighted 变量加 weight
    for cp in bins_def["coverpoints"]:
        for b in cp["bins"]:
            entry = next((e for e in functional["per_coverpoint"]
                          if e["name"] == cp["name"]), None)
            if entry and not any(bb["covered"] for bb in entry["bins"]
                                  if bb["name"] == b["name"]):
                # 任意 weighted 变量权重 +5
                for v in constraints["random_variables"]:
                    if v.get("dist") == "weighted":
                        for k in v["weights"]: v["weights"][k] += 5
                        break
    return constraints
```

**Pipeline 调用**（`pipeline.py`）：
```
run tb → 收集 C
while iter < MAX_ITER and C < TARGET_C:
    feedback.adjust(constraints, bins, functional, iter)
    重新跑 generate + simulate + coverage
    更新 C
```

---

## 10. Stage 8: Reporter（`reporter.py`）

### 10.1 算法

```python
@dataclass
class ReporterInputs:
    design, skeleton, constraints, bins, functional, cov_result,
    stages, reproducible_command, tool, failures, out_dir

def write_all(inputs):
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.joinpath("design.json").write_text(_pretty(inputs.design))
    out_dir.joinpath("verification_skeleton.json").write_text(_pretty(inputs.skeleton))
    out_dir.joinpath("constraints.json").write_text(_pretty(inputs.constraints))
    out_dir.joinpath("coverage_bins.json").write_text(_pretty(inputs.bins))
    out_dir.joinpath("functional_coverage.json").write_text(_pretty(inputs.functional))
    out_dir.joinpath("coverage_result.json").write_text(_pretty(inputs.cov_result))
    report = build_report(inputs)
    out_dir.joinpath("report.json").write_text(_pretty(report))
```

### 10.2 `report.json` 内容

```python
{
    "stages": {"parse": "ok/fail", "skeleton_gen": ..., "simulate": ..., "coverage_collect": ...},
    "outputs": {7 个 JSON 的相对路径},
    "coverage_summary": {line, branch, functional, combined_C},
    "reproducible_command": "./run.sh --rtl ... --top ... --seed N --num-seq 5000",
    "environment": {
        "python": platform.python_version(),
        "tool": "verilator-5.020" / "iverilog-...",
        "docker_image": "verif-agent:0.1.0",
        "platform": "linux/x86_64",
    },
    "failures": [最多 50 条 mismatch],
}
```

`assert_all_outputs_present(out_dir)` 检查 7 个文件均存在。

---

## 11. Pipeline 编排（`pipeline.py`）

### 11.1 单次调用

```python
def run(rtl_dir, top, out_dir, seed, num_seq=5000, timeout_sec=600) -> PipelineResult:
    stages = {parse, skeleton_gen, simulate, coverage_collect}  # 初始 pending
    failures = []
    try:
        design = resolve(rtl_dir, top)         # Stage 1
        classify(design)                       # Stage 2
        stages["parse"] = "ok"

        bins = define(design)                  # Stage 3a
        constraints = generate(design, seed, num_seq)

        rend = render(design, constraints, bins, out)   # Stage 4
        stages["skeleton_gen"] = "ok"

        sim_result, tool = run_simulation(rend.tb_dir, seed, num_seq, timeout_sec)  # Stage 5
        stages["simulate"] = "ok" if sim_result.ok else "fail"

        line_h, line_t, br_h, br_t = _read_line_branch(sim_result, tool)   # Stage 6
        raw_func = parse_cocotb_json(sim_result.functional_cov)
        functional = reconcile_with_bins(raw_func, bins)
        cov_result = compute_combined(line_h, line_t, br_h, br_t,
                                       functional["covered_bins"], functional["valid_bins"])
        stages["coverage_collect"] = "ok"

        # Stage 7: 反馈
        for it in range(1, MAX_ITER + 1):
            if cov_result["combined_C"] >= TARGET_C: break
            adjust(constraints, bins, functional, it)
            rend = render(design, constraints, bins, out)
            sim_result, tool = run_simulation(rend.tb_dir, seed, num_seq, timeout_sec)
            line_h, line_t, br_h, br_t = _read_line_branch(sim_result, tool)
            raw_func = parse_cocotb_json(sim_result.functional_cov)
            functional = reconcile_with_bins(raw_func, bins)
            cov_result = compute_combined(...)

        write_all(...)                          # Stage 8
        return PipelineResult(ok=True, ...)
    except Exception as exc:
        stages = mark_failed(stages, stage_of_error(exc))
        write_all(...)   # 至少写一部分产物
        return PipelineResult(ok=False, error=str(exc))
```

### 11.2 数据流总结

```
rtl_dir ─▶ [rt_parser]            ─▶ Design ─▶ [classifier]           ─▶ Design (annotated)
                                                                                              │
                     ┌──────────────────────────────────────────────────────────────────────┘
                     ▼
[coverage_definer]  ─▶  coverage_bins.json (coverpoints/bins)
[constraints_gen]   ─▶  constraints.json     (seed, num_seq, random_vars)
                     │
                     ▼
[tb_gen.render]     ─▶  generated_tb/{tb_top.py, dut_inst.v, Makefile, coverpoints.py, sim_run.log}
                     ─▶  verification_skeleton.json
                     ─▶  design.json
                     │
                     ▼
[sim.run_simulation] ─▶  RunResult(exit_code, coverage_dat, coverage_xml, functional_cov)
                     │
                     ▼
[coverage.parse]    ─▶  (line, branch, functional_covered, functional_valid)
                     │
                     ▼
[feedback]          ─▶  (optional) updated constraints
                     │
                     ▼
[reporter.write_all] ─▶  7 个 JSON 写到 <out>/
```

---

## 12. Cocotb 内部循环（5 段时间线）

虽然不在 verif_agent 包内，但理解运行中的协程顺序对排错很关键：

```
T0: make 调 verilator --coverage --build
    ↳ Verilator 把 dut.v + dut_inst.v 编译成 C++/DPI 库

T1: 生成的可执行二进制加载 Python（cocotb runner）
    ↳ Python 进程打开 <libvvp> 或 verilator_run.so

T2: Python `import tb_top` 触发 @cocotb.test() 注册

T3: cocotb 启动协程：
    a. `setup_clock_reset(dut)` → 起 10ns clock，复位 5 周期
    b. `cocotb.start_soon(<monitor_name>(...))` ← 协程开始采样
    c. `await <driver_name>(dut, RNG, NUM_SEQ)` ← 跑 5000 序列
    d. `await Timer(200, units='ns')` ← 等最后几个事务落地
    e. `sb.check()` ← 一次性比对
    f. `_dump_functional_cov(...)` ← 写 generated_tb/functional_cov.json
    g. `assert passed, ...` ← fail → test fails with mismatch log

T4: verilator 写 coverage.dat + coverage.xml

T5: pipeline 读 coverage.* + functional_cov.json + reporter.write_all
```

---

## 13. 关键 invariant（破坏任何一条会让某电路 0 分）

| # | invariant | 强制位置 |
|---|---|---|
| 1 | `functional_coverage.json` 的 cp/bin 名字必须与 `coverage_bins.json` 一一对齐 | `coverage/functional.py:reconcile_with_bins` |
| 2 | 每个 bin 必须有 `sampling_condition`（无 → 不计入分母） | `coverage_definer._enforce_sampling_condition` |
| 3 | `ports` 列表在常规 RTL 上**永远**非空 | `regex_parser.parse()`（兜底）|
| 4 | testbench 至少 1 个 driver + 1 个 monitor（含 generic 兜底） | `protocols/__init__.py` 默认 generic + `render.skeleton` |
| 5 | `verification_skeleton.json` 里的 driver/monitor/scoreboard **名字**在 `tb_top.py` 里真的存在 | `ProtocolOutput.driver_name / monitor_name / scoreboard_name`（显式声明）|
| 6 | 端口按名连（`dut_inst.v`），漏连/错连导致 verilator 编译失败 | `_verilog_wrapper()` |
| 7 | `report.json` 必须含 4 个 stage 标志 + `reproducible_command` | `reporter.write_all()` |
| 8 | 同 seed 跑两次 JSON 字节相同（除时间戳） | 单一 RNG 实例 + 冻结依赖版本 |

---

## 14. 扩展点

| 想加什么 | 该改什么 |
|---|---|
| 新协议（比如 I2C/SPI）| 加 `tb_gen/protocols/i2c.py` + 在 `PROTOCOL_REGISTRY` 注册 + 在 `classifier.py` 加信号集 + 在 `coverage_definer.py` 加 `_i2c_bins` |
| 改 Z3 求覆盖 | 加 `coverage/z3_target.py`：`parse_uncovered_lines(cov.xml) → z3.Solver() → driver 入参`。`feedback.py` 调它 |
| 换协议识别变体 | 改 `classifier._strip_axi_prefix._AXI_PREFIXES` |
| 改 C 公式 | 改 `coverage/aggregator.py:compute()` |
| 改输出 schema | 改 `verif_agent/schemas/__init__.py` —— schema 文件会自动重写 |

---

## 15. 调试 cheat-sheet

| 症状 | 看哪个文件 | 看哪里 |
|---|---|---|
| `parse: fail` | `verif_agent/rtl_parser/regex_parser.py` | 看是否能匹配你的风格 |
| `parse: fail` (PyVerilog) | `verif_agent/rtl_parser/pyverilog_parser.py` | 是它抛的；fallback 到 regex 仍可能 OK |
| `skeleton_gen: fail` | `verif_agent/tb_gen/render.py` | 看 stdout/stderr |
| `simulate: fail` 且 verilator 还在 | `tb_top.py` / `Makefile` | 看 verilator 报错 |
| `simulate: fail` 且已切 Icarus | `report.json.environment.tool = "iverilog"` | 已知 Icarus 不支持 `specify` 等 |
| `coverage: 0` | `functional_cov.json` 没生成 | cocotb 在 `setup_clock_reset` 后 0 cycle 就退出了？|
| `functional_cov.json` 有但 `coverage_result.json` 全 0 | `coverage/functional.py:reconcile_with_bins` | bin 名字对不上 |
| 真值/期望值不匹配 | `report.json.failures[]` | 看前 50 条 |

---

## 16. 测试覆盖

`tests/` 下 55 个 pytest，每个都对应上文的某段：

| 测试文件 | 对应阶段 |
|---|---|
| `test_parser.py` | §3 RTL 解析 |
| `test_classifier.py` | §4 协议分类 |
| `test_constraints_and_bins.py` | §5 constraints / bins |
| `test_protocols.py` | §6 testbench 生成 |
| `test_schemas.py` | §8 spec 7 条硬约束 |
| `test_generic_fallback.py` | §6 Plan A 兜底 + Plan B APB |
| `test_skeleton_consistency.py` | §6.4 骨架/代码名字一致（最关键的新增）|
| `test_bin_validity.py` | §5.2 sampling_condition 强制 |
| `test_cli.py` | `cli.py` 入口冒烟 |
| `test_case1_smoke.py`（需 simulator）| 端到端 case1 |
| `test_reproducibility.py`（需 simulator）| 同 seed 跑两次 diff 字节 |
| `test_fallback.py`（需 simulator）| Verilator→Icarus fallback |

---

## 17. 复现命令

```bash
# 本地 Python 入口
./run.sh --rtl public_dataset/case1/rtl --top stream_dut \
         --out /tmp/case1 --seed 42 --num-seq 5000

# Docker 入口（推荐评测环境）
docker build -t verif-agent:0.1.0 .
docker run --rm -v "$PWD:/work" verif-agent:0.1.0 \
  --rtl benchmark/rtl --top dut --out submission_out/case \
  --seed 12345 --num-seq 5000

# 测试
pytest tests/ -q
```
