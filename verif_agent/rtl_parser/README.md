# rtl_parser — RTL 解析子系统

把 `--rtl` 目录下的 Verilog 解析成 `Design` 对象（端口 / 参数 / 时钟 / 复位），
供 `classify` / `constraints_gen` / `coverage_definer` / `tb_gen` 消费。
对外只暴露一个入口：`port_resolver.resolve(rtl_dir, top)`。

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 门面，re-export `resolve` / `discover_rtl_files` |
| `preprocess.py` | 预处理：剥注释 / 续行 / 展开 `` `include `` / 简化 `` `ifdef ``（主要服务 regex 路径） |
| `pyverilog_parser.py` | **主解析器**：pyverilog AST，精确（方向 / 宽度 / 参数） |
| `regex_parser.py` | **兜底解析器**：正则，pyverilog 失败时用 |
| `port_resolver.py` | **调度 / 总装**：遍历文件、跑两路、merge、推断 clk/rst |

## 解析流程（`resolve`）

```
resolve(rtl_dir, top)
 ├─ discover_rtl_files         找所有 .v/.sv/.vh
 ├─ _topological_sort          按 `` `include `` 关系排序
 └─ for each file:
      ├─ preprocess(src)                       剥注释 / 续行 / include / ifdef
      ├─ pyverilog_parser.parse(src, f, top)   ← 主路径（截头 + top 过滤）
      ├─ regex_parser.parse(text, f, top)      ← 兜底（top 过滤）
      └─ _merge_ports(pyv, regex, pyv_params)  pyverilog 胜
 ├─ _infer_clock_reset         从端口名猜 clk / rst
 └─ return Design(top, ports, params, clock, reset, ...)
```

## 关键算法决策

### 1. pyverilog 截头（`_extract_headers`）
pyverilog 1.3.0 的 LALR 全文件解析对 body 里的常见写法（如 `reg [1:0] a = X, b;`）
会整体 `ParseError`，连端口都拿不到。而端口全在 module 头 `module name #(...) (...);` 里。
**做法**：剥注释后，从每个 `module` 关键字起做括号深度跟踪，截到第一个 depth-0 的 `;`，
**只把 header 喂给 pyverilog**，body 全部丢弃 —— 绕过一切 body 级语法不兼容。

### 2. 按 `top` 过滤（`parse` 的 `top` 参数）
多文件设计（如 case2 的 `case2.v` + `case2_1.v` + `case2_2.v`）里，只有顶层 module
（`axis_fifo_adapter`）的端口该进 `Design.ports`，子模块（`axis_fifo` / `axis_adapter`）
的端口不该混入。`parse(text, filename, top)` 只取 `module.name == top` 的那个 module，
跳过所有子模块 —— 这是多文件 case 端口表干净的前提。

### 3. 参数化表达式求值（`_eval` / `_vint`）
pyverilog 给的宽度是 AST 节点（`Minus(Identifier "ID_WIDTH", IntConst "1")`），不是 int。
`_eval` 递归求值：

- `IntConst` → `_vint`：解析 Verilog 字面量（`32'd2` / `8'hFF` / `7` → int）
- `Identifier` → 查参数表
- `Minus` / `Plus` / `Times` / `Divide` / … → 递归求左右子树再算
- `Rvalue` → 解包（取 `children()[0]`）

参数按**声明顺序**求值，所以后声明的参数能引用前面的
（`S_STRB_WIDTH = S_DATA_WIDTH/8` → 4）。代入后端口宽度
`[ID_WIDTH-1:0]` → `[8-1:0]` → 宽度 8。

### 4. 双解析器 + merge 优先级
- **pyverilog（主）**：精确，但易碎（pyverilog API / 语法兼容性）。失败抛 `ParseError`。
- **regex（兜底）**：总能产出“something”，但不精确（参数化 module 头的嵌套括号易错）。
- **merge（`_merge_ports`）**：以 regex 为 base，pyverilog 覆盖 direction / width / sign，
  并把 pyverilog 的参数作为权威来源补进来。pyverilog 失败时 regex 独立支撑，保证端口表非空。

### 5. pyverilog LALR 表隔离
pyverilog 每次 parse 会现算 LALR 表（`parsetab.py` / `parser.out`），默认写 cwd
（会污染仓库根）。`parse()` 用 `tempfile.mkdtemp()` 当 `outputdir`，把表隔离在临时目录。

### 6. case 名推断（在 `pipeline`，非此处）
`pipeline._infer_case_name(rtl_dir)`：从 `--rtl` 路径取 case 名
（`.../case1/rtl` → `case1`），供 design.json 的 `name` 字段使用。

## 数据结构（`design.py`）

- `Design`：top / rtl_files / clock[] / reset[] / parameters[] / ports[] /
  inferred_protocols / primary_protocol / description（LLM 生成的描述）
- `Port`：name / direction / width / sign / **protocol_group**（classifier 标）/ role
- `Parameter`：name / value / width / signed
- `Clock` / `Reset`：name / period_ns / active_level / duration_cycles

## 已知限制

- regex 兜底对“参数默认值带括号”的 module 头会匹配失败
  （`_MODULE_HDR_RE` 的 `[^\)]*` 不处理嵌套括号）；由 pyverilog 主路径兜住。
- `preprocess.py` 的 `` `ifdef `` 简化只认 allowlist 宏，`` `define `` 不展开 ——
  但 module 头里一般没有，影响很小。
- non-ANSI 端口（端口名在头、方向/宽度在 body）：regex 兜底只能近似，
  pyverilog 主路径更准。
