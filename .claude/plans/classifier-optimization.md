# Classifier 优化计划

## 目标

1. **AXI 信号识别改为反向推导**（万无一失，不依赖前缀穷举）
2. **passive 端口按前缀聚类**（cfg_→configuration, sts_→status）
3. **LLM 给前缀聚类起语义化名字**（确定性聚类 + 语义化命名）
4. **Port 新增 `interface_name` 字段**（直接记录接口归属，reporter 不再硬匹配）

## 改动文件

### 1. `verif_agent/design.py` — Port 新增字段

```python
@dataclass
class Port:
    name: str
    direction: Direction
    width: int
    sign: Sign = "unsigned"
    protocol_group: str = "unknown"
    role: Role = "passive"
    interface_name: str = ""    # NEW: 接口归属，如 "s_axi", "input", "configuration"
```

`to_dict()` 自动包含。

### 2. `verif_agent/classifier.py` — 核心重构

#### 2a. AXI 信号反向推导（替换 `_strip_axi_prefix` + `_AXI_PREFIXES`）

删除 `_AXI_PREFIXES` 穷举列表，新增 `_reverse_match(name, signal_sets)`:

```python
def _reverse_match(name: str, signal_sets: dict[str, set[str]]) -> tuple[str | None, str | None]:
    """从端口名末尾匹配已知信号名，返回 (前缀, 信号名)。
    
    匹配规则：name == signal 或 name 以 "_" + signal 结尾。
    最长信号名优先（避免 "awvalid" 被误匹配为 "valid"）。
    """
    ln = name.lower()
    all_signals = {}  # signal_name -> (channel_group, signal_set_name)
    for group_name, sigs in signal_sets.items():
        for s in sigs:
            all_signals[s] = group_name
    
    # 按信号名长度降序排列，最长优先
    for sig in sorted(all_signals, key=len, reverse=True):
        if ln == sig or ln.endswith("_" + sig):
            prefix = ln[:-len(sig)].rstrip("_") if ln != sig else ""
            return prefix, sig
    return None, None
```

对 AXI 使用：
```python
_AXI_SIGNAL_MAP = {
    "axi_aw": _AXI_AW_SIGNALS,
    "axi_w":  _AXI_W_SIGNALS,
    "axi_b":  _AXI_B_SIGNALS,
    "axi_ar": _AXI_AR_SIGNALS,
    "axi_r":  _AXI_R_SIGNALS,
    "axi_a":  _AXI_A_SIGNALS,
}
```

同理对 APB、AXI-Stream 也用反向推导。

#### 2b. Classifier 新增 pass3: 前缀聚类

在现有 pass1（协议检测）和 pass2（per-port 标注）之后，新增 pass3:

```python
def _cluster_passive_ports(design: Design) -> dict[str, list[Port]]:
    """收集所有 protocol_group == "passive" 的端口，按前缀聚类。
    
    前缀 = 端口名按 '_' 分割的第一段。
    前缀只有 1 个端口 → 不聚类，保持 passive。
    前缀 >= 2 个端口 → 归为同一组，protocol_group = "custom:<prefix>"。
    """
```

例如 case3：
- `cfg_fifo_base_addr`, `cfg_fifo_size_mask`, `cfg_enable`, `cfg_reset` → 前缀 `cfg`, 4个 → `custom:cfg`
- `sts_fifo_occupancy`, `sts_fifo_empty`, `sts_fifo_full`, `sts_reset`, `sts_active`, `sts_write_active`, `sts_read_active` → 前缀 `sts`, 7个 → `custom:sts`
- `input_watermark` → 前缀 `input`, 1个 → 保持 passive
- `input_rst_out` → 前缀 `input`, 1个 → 保持 passive（如果已被 rst 拿走则跳过）

阈值：前缀组 >= 2 个端口才聚类。

#### 2c. 接口名赋值（pass4）

统一为每个端口赋 `interface_name`：

- **AXI**: 前缀相同 → 同一接口。前缀空 → `"axi"`。
  - `s_axi_awvalid` → 前缀 `s_axi` → interface `"s_axi"`
  - `m_axi_araddr` → 前缀 `m_axi` → interface `"m_axi"`
  - `awvalid` → 前缀空 → interface `"axi"`
- **AXI-Stream**: 同 AXI 逻辑，前缀决定接口名
- **Stream (valid/ready)**: stem 就是接口名（`in_valid` → interface `"in"`）
- **SRAM**: `"sram"`
- **APB**: `"apb"`
- **custom:xxx**: 接口名暂用前缀原始名，后续由 LLM 覆盖
- **clk/rst/passive**: `interface_name = ""`

### 3. `verif_agent/llm_describe.py` — 新增接口命名功能

在 `_build_prompt` 中增加对 custom 前缀的命名请求：

```python
# 收集所有 custom:xxx 前缀
custom_prefixes = set()
for p in design.ports:
    if p.protocol_group.startswith("custom:"):
        custom_prefixes.add(p.protocol_group.split(":", 1)[1])

if custom_prefixes:
    # 在 prompt 中加入命名请求
    prompt += f'\n\nAlso name these port groups: {sorted(custom_prefixes)}\n'
    prompt += 'Add a "custom_interface_names" key: {"prefix": "semantic_name", ...}\n'
```

在 `describe()` 返回结果中提取 `custom_interface_names`，回填到 Port 的 `interface_name`。

### 4. `verif_agent/reporter.py` — 使用 interface_name

`_iface_of_port()` 简化：直接读 `port["interface_name"]`，不再硬匹配前缀。

`_build_interfaces()` 用 `interface_name` 分组。

`_iface_role()` 保留，但也能从 design.description 中读取 LLM 给的 role。

### 5. `verif_agent/constraints_gen.py` — 使用 interface_name

constraints 里的 random_variable 按 interface_name 分组。

## 改动顺序

1. `design.py` — Port 新增 `interface_name` 字段
2. `classifier.py` — 反向推导 + 前缀聚类 + 接口名赋值
3. `llm_describe.py` — 新增 custom 接口命名
4. `reporter.py` — 使用 interface_name
5. 测试 case1-5 验证

## 不改的文件

- `coverage_definer.py` — 后续步骤再改
- `constraints_gen.py` — 后续步骤再改
- `tb_gen/` — 后续步骤再改
