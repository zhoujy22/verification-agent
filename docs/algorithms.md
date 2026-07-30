# verif_agent 主要算法说明

本文档描述 `verif_agent` 代码库的核心算法。该工具是一个 **RTL 验证环境自动生成 Agent**：给定 Verilog RTL，自动解析接口、分类协议、生成约束随机 testbench、运行仿真、采集覆盖率，并通过覆盖率反馈循环迭代优化，最终输出 7 个 JSON 报告与自包含的 cocotb testbench。

整体流水线由 `verif_agent/pipeline.py` 编排：

```
parse → classify → coverage_definer → constraints_gen → render →
simulate (Verilator→Icarus fallback) → collect coverage →
feedback (max 2 iterations) → reporter.write_all
```

---

## 1. RTL 解析算法（`rtl_parser/port_resolver.py`）

目标：从 `--rtl` 目录解析出顶层模块的端口、参数、时钟、复位，产出 `Design` 对象。

### 1.1 文件发现与拓扑排序

- `discover_rtl_files`：递归收集 `.v/.sv/.vh/.svh` 文件，按文件名排序保证稳定性，去重。
- `_topological_sort`：解析每个文件中的 `` `include "xxx" `` 指令，建立依赖图，做 DFS 后序排序，使被包含的文件排在包含者之前（best-effort）。

### 1.2 多解析器融合（主路径 + 回退）

采用 **pyslang 优先、pyverilog+regex 回退** 的两级策略：

- **主路径（pyslang）**：将所有源文件加载进一个 `Compilation`，原生支持跨文件参数引用与顶层例化解析，语义最完整。
- **回退路径（`_legacy_per_file_parse`）**：逐文件执行 `preprocess → pyverilog → regex`，二者结果合并。

### 1.3 端口/参数合并算法（`_merge_ports`）

regex 与 pyverilog 各有盲区，合并规则：

1. **regex 基线**：regex 总能成功，先建立端口/参数的基线字典（`direction / width / sign`）。
2. **参数化宽度求解（`_resolve_widths`）**：把宽度表达式中的参数名替换为已知整数值，尝试 `MSB:LSB` 形式或纯整数形式，用受限 `eval` 求值（`__builtins__` 置空），取 `max(msb-lsb+1, 1)`。
3. **pyverilog 增强**：pyverilog 在类型字段（`direction/width/sign`）上权威，覆盖 regex；regex 静默时由 pyverilog 补齐。参数以 pyverilog 为权威源（regex 常因括号默认值漏掉参数化模块头）。
4. **声明顺序恢复**：按 regex 解析出的声明顺序排列端口，pyverilog-only 的端口追加到末尾，保持 RTL 声明顺序。

### 1.4 时钟/复位推断（`_infer_clock_reset`）

复用 classifier 的检测器（见 §2.2），保证后缀命名信号（`input_clk`、`rst_req_out`）也能识别，避免多时钟域设计（如 case3）坍缩成单时钟。复位有效电平由名称后缀推断：`_n` / 末尾 `n` → 低有效，否则高有效。

---

## 2. 协议分类算法（`classifier.py`）

目标：为每个端口打上 `protocol_group / role / interface_name` 标签，供后续 testbench 生成与覆盖率定义使用。采用 **四遍扫描**。

### 2.1 反向匹配（`_reverse_match`）—— 核心原语

协议识别对前缀鲁棒的关键。对每个端口名，与已知信号名集合做后缀匹配：

- 规则：`name == signal` 或 `name.endswith('_' + signal)`
- **最长信号名优先**：候选按长度降序排序，避免 `awvalid` 错误匹配到 `valid`。
- 返回 `(prefix, signal_name, channel_group)`，例如 `s_axi_awvalid → ("s_axi", "awvalid", "axi_aw")`。

### 2.2 Pass 1：全局协议成员判定

对全部端口名集合，分别判定各协议是否存在：

- **AXI**（`_classify_axi`）：通过反向匹配剥离前缀得到 bare 信号集。写通道完整（AW+W+B）或读通道完整（AR+R）即判为 AXI；再根据是否存在 burst 指示信号（`awsize/awburst/awlen/awid` 等）区分 `AXI4` 与 `AXI4-Lite`。纯统一地址通道（`a*` 无数据通道）判为非 AXI，交给 generic driver。
- **APB**：核心 4 信号（`psel/penable/pwrite/pready`）全在即判为 APB。
- **SRAM**：存在片选信号且数据/控制信号 ≥ 3 个。
- **valid/ready stream**：同时存在 `_valid`、`_ready`、`_data` 后缀。
- **AXI-Stream**：`tvalid + tready + tdata` 全在。

### 2.3 Pass 2：逐端口分配 group/role

按优先级依次尝试：时钟 → 复位 → AXI → 纯 `a*` 解码器 → APB → SRAM → stream → AXI-Stream → passive。

每个协议有独立的 **角色判定函数**（driver/monitor/passive），依据信号在协议中的语义：
- AXI：`*ready` 多为 monitor（slave 驱动），`*valid/addr/...` 为 driver；B/R 通道方向反转。
- stream：`_valid/_data` 在输入侧为 driver、输出侧为 monitor；`_ready` 反之。
- AXI-Stream：以 `m_axis_` 前缀区分输出（driver 驱 `tready`）与输入。

### 2.4 Pass 3：被动端口按前缀聚类（`_cluster_passive_ports`）

未被任何协议认领的 passive 端口，按首个 `_` 分隔的前缀分组；同前缀且 ≥ 2 个成员的组重分类为 `custom:<prefix>`，input → driver、output → monitor。这使自定义配置/状态总线也能被驱动。

### 2.5 Pass 4：接口名分配（`_assign_interface_names`）

依据 group 与反向匹配得到的前缀，为每个端口分配 `interface_name`（如 `s_axi`、`m_axis`、`apb`、stream 的 stem 名），供 skeleton 分组与多接口 driver 拆分使用。

---

## 3. 约束随机策略生成算法（`constraints_gen.py`）

目标：产出 `constraints.json`，确定性依赖于 `(seed, design, num_seq)`。

### 3.1 确定性 RNG 派生

`_rng_choices(seed, scope)` = `(seed ^ sha256(scope)[:4字节]) & 0x7FFFFFFF`，保证同一 design+seed 下分布/权重 shaping 稳定可复现。

### 3.2 随机变量生成

遍历所有 `role == "driver"` 的端口，为每个生成随机变量描述：
- `range = [0, 2^width - 1]`，默认 `dist = uniform`。
- **协议特定约束注入**（按信号名匹配）：
  - `awaddr/araddr`：4 字节对齐（`align=4`）。
  - `wstrb`：加权分布，`0xF` 权重 60、`0x0` 权重 12 等，偏向合法全写同时保留边界用例。
  - `awburst`：`INCR:70 / FIXED:20 / WRAP:10`。
  - `awsize`：`4B:80`，其余各 5。
- 存在 AXI 类端口时追加全局约束 `no_excessive_outstanding`（inflight read/write ≤ 4）。

---

## 4. 功能覆盖率 bin 定义算法（`coverage_definer.py`）

目标：产出 `coverage_bins.json`，每个 bin 必须有显式 `sampling_condition`（spec §34 强制校验，`_enforce_sampling_condition` 缺失即抛错）。

### 4.1 协议族分派

按 `design.inferred_protocols` 分派到对应 bin 生成器：AXI4/AXI4-Lite → `_axi_bins`、SRAM → `_sram_bins`、stream → `_stream_bins`、APB → `_apb_bins`、AXI-Stream → `_axis_bins`。无识别协议时回退到单 `BIN_TICK`（generic）。

### 4.2 通道能力感知（AXI 关键优化）

`_axi_read_caps` 从 classifier 的 channel 标注推断 `(has_read, has_write)`。**只生成 DUT 实际具备的通道对应的 bin**——避免对只读适配器（case1 仅有 AR+R）生成写 strobe/写响应 bin 而永远无法命中、功能覆盖率卡在 0。

### 4.3 bin 设计哲学

每个 bin 是一个 **事务特征**（burst length 区间 / size / 地址对齐 / burst 类型 / backpressure / 响应码），由 cocotb driver 在发起事务时直接记录，与参考 testbench 的 FunctionalCoverage 方法对齐。地址解码器（case5，纯 `a*` 无数据通道）有专用 `_addr_decoder_bins`（select/decode-error/completion）。

---

## 5. Testbench 生成算法（`tb_gen/render.py` + `tb_gen/protocols/`）

目标：生成自包含 cocotb testbench（`tb_top.py / dut_inst.v / Makefile / coverpoints.py`）。

### 5.1 协议生成器注册表（`protocols/__init__.py`）

`PROTOCOL_REGISTRY` 按 `primary_protocol` 分派到具体生成器（axi_full/axi_lite/axi_stream/sram/stream/apb/generic）。`""` 与 `"passive"` 均映射到 generic 兜底（Plan A：catch-all，永不留空）。

### 5.2 Verilog wrapper 生成（`_verilog_wrapper`）

**关键算法决策**：DUT 输入端口声明为内部 `reg`（而非 `tb_top` 的 `input wire` 端口）。若声明为端口，仿真器下无外部驱动源，cocotb 的 `dut.<sig>.value = ...` 写入会被 VPI 静默丢弃，DUT 恒见 0/X。声明为 `reg` 使 cocotb 写入立即生效，与参考 cocotb 顶层一致。输出端口声明为 `wire`。

### 5.3 cocotb 主程序组装（`_tb_top_py`）

模板化拼装：coverpoint 注册表（`Hit()` 计数对象）→ clock/reset 片段 → 协议 driver/monitor/scoreboard/coverpoint 源码 → `@cocotb.test()` 主体（按协议选择对应的 driver+scoreboard 调用序列）→ 末尾 `_dump_functional_cov` 将 bin 命中数写入 `functional_cov.json`。

### 5.4 多时钟/复位处理（`clock_reset.py`）

- 单时钟：紧凑 legacy 形式，启动一个 `cocotb.Clock`，在时钟沿上脉冲复位。
- 多时钟：为每个时钟各起一个 `cocotb.Clock`；所有复位先置空闲电平，在主时钟（`clk`，AXI 域）沿上同步脉冲全部复位，保证多时钟域（case3 的 clk/input_clk/output_clk + 多复位）同时就绪。

### 5.5 自包含交付

RTL 源文件复制进 `generated/rtl/`，Makefile 以 `$(PWD)` 绝对路径引用，使 `generated/` 目录可拷走即用，不依赖原 `--rtl` 绝对路径。

### 5.6 skeleton 一致性

`verification_skeleton.json` 的 driver/monitor 名直接取自协议生成器中真实存在的 Python 标识符（`proto.driver_name` 等），并按 `interface_name` 分组列出 drives/observes 端口，确保 skeleton 与 `tb_top.py` 实际内容一致。

---

## 6. 仿真执行算法（`sim/`）

### 6.1 仿真器回退（`run_simulation`）

**Verilator 优先，Icarus 回退**：Verilator 失败（未安装/编译错/超时）则自动切到 iverilog，对调用方透明。

### 6.2 Verilator 覆盖率采集（`runner_verilator.py`）

通过生成的 Makefile 驱动 `make`（`SIM=verilator`，`--coverage-line --coverage-toggle`）。仿真后用 `verilator_coverage --write-info` 将二进制 `coverage.dat` 转为 lcov `.info`，否则二进制 `.dat` 会被误解析为 0% 覆盖率。

---

## 7. 覆盖率采集与综合算法（`coverage/`）

### 7.1 行/分支覆盖率解析（`line_branch.py`）

多格式回退链（`_read_line_branch`）：
1. **`parse_verilator_dat`**（首选）：直接解析 Verilator 原生 `coverage.dat`，用正则匹配控制字符分隔的 `v_line` / `v_branch` 记录。**这是 Verilator 下获取非零分支覆盖率的唯一途径**——`--write-info` 生成的 lcov 只含 `DA:`（行）而丢弃 `BRDA:`（分支）。排除 `dut_inst.v / tb_top.*` 等生成物。
2. `parse_verilator_info`：解析 lcov `.info`（`DA:` 行、`BRDA:` 分支），排除生成 wrapper。
3. `parse_verilator_xml`：解析 `coverage.xml`。
4. `parse_icarus_dat`：iverilog `.dat` 的 best-effort 解析（仅行，分支不可得）。

### 7.2 功能覆盖率对账（`functional.py`）

`reconcile_with_bins`：以 `coverage_bins.json` 的 cp/bin 层级为权威，将 cocotb 输出的 `functional_cov.json` 命中数对齐回去。无 `sampling_condition` 的 bin 丢弃（spec §34）。`hit_count > 0` 即判 covered，统计 `covered_bins / valid_bins / functional_coverage_pct`。

### 7.3 综合覆盖率公式（`aggregator.py`）

```
C = 0.4 × 行覆盖率 + 0.3 × 分支覆盖率 + 0.3 × 功能覆盖率
```

（spec §112/§113）。各项均为 `100 × hits / max(total, 1)`。

---

## 8. 覆盖率反馈循环算法（`feedback.py` + pipeline Stage 5）

目标：若首轮 `C < 85%`，通过调整约束权重并重仿真来提升覆盖率，最多 `MAX_ITER = 2` 次额外迭代。

### 8.1 反馈调整（`adjust`）

1. 在 `constraints["coverage_feedback_updates"]` 追加本次迭代记录。
2. 遍历 bins 定义中每个 bin，在对账结果里查其是否 covered。
3. 对未命中 bin，best-effort 找到一个 `dist == "weighted"` 的随机变量，对其每个已有权重 `+5`（轻微偏向，扩大命中面），找到即 break（每次只调一个变量，避免过度偏移破坏随机性）。

### 8.2 循环控制（pipeline）

```
for it in 1..MAX_ITER:
    if combined_C >= TARGET_C(85%): break
    feedback_adjust(constraints, bins, functional_coverage, it)
    re-render tb → re-simulate → re-collect → recompute C
```

仿真失败则记录并 break。每次重渲染会基于更新后的 constraints 重新生成 testbench。

---

## 9. 报告与可复现性（`reporter.py`）

`write_all` 汇总 7 个 JSON：`design.json`（接口解析）、`verification_skeleton.json`（driver/monitor/scoreboard）、`constraints.json`、`coverage_bins.json`、`functional_coverage.json`、`coverage_result.json`（行/分支/功能/综合）、`report.json`（阶段状态 + 可复现命令 + 失败日志摘要）。

- **可复现命令**：`_reproducible_cmd` 重建 `./run.sh --rtl ... --top ... --out ... --seed ... --num-seq ...`。
- **失败抓取**：`_scrape_failures` 从 `sim_run.log` 抓取含 `MISMATCH/ERROR/FAIL/scoreboard` 的前 50 行。
- **部分输出**：流水线任意阶段异常时仍写部分报告（`_mark_failed_stage` 标记失败阶段），便于调试。

---

## 算法特性总结

| 特性 | 实现要点 |
|---|---|
| 前缀鲁棒的协议识别 | 反向匹配 + 最长信号名优先 |
| 多解析器融合 | pyslang 主路径 + pyverilog/regex 回退，声明顺序恢复 |
| 确定性可复现 | seed 派生 RNG，固定 `num_seq=5000` |
| 覆盖率不卡 0 | 通道能力感知只生成可达 bin；直接解析 `.dat` 取真实分支覆盖 |
| 覆盖率自举 | `C<85%` 反馈调权 + 重仿真，最多 2 轮 |
| 仿真器容错 | Verilator→Icarus 自动回退 |
| 自包含交付 | RTL 复制进 `generated/`，路径全相对 |
| skeleton 一致 | driver/monitor 名取自真实生成代码标识符 |
