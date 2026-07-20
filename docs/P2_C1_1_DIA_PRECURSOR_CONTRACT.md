# P2-C1.1：DDA selected precursor 与 DIA isolation window 统一核心合同

日期：2026-07-17（Asia/Shanghai）

## 1. 决策结论

`core_precursors` 保持为九个顶层块之一，一个 `.zp` 仍只对应一个 run。
本阶段只扩展该块内记录的逻辑合同，不改变 v1/v2 物理格式。记录分为：

~~~text
selected_precursor
isolation_window
~~~

DDA selected precursor 表示一个明确被选择的前体，因此需要唯一 m/z、强度
和正整数 charge。DIA MS2 表示一个采集窗口，窗口内可同时存在多个、不同
charge 的前体；整个 MS2 没有唯一 charge，不能用 0、1、鉴定结果 charge 或
统计值伪造。

## 2. 逻辑字段

`PrecursorBlock` 继续复用原有 `precursor_mz` 名称，并新增：

~~~text
precursor_kind: selected_precursor | isolation_window | null
isolation_lower_mz: float | null
isolation_upper_mz: float | null
~~~

`charge`、`precursor_mz` 和 `intensity` 的类型允许为空，但是否可空由 kind
条件决定，不是无条件 Optional。

### 2.1 selected_precursor

`precursor_kind` 可显式为 `selected_precursor`，也可在旧记录中省略。两者
逻辑等价。规则为：

- `charge` 必须是正整数；缺失、null、0、负数和 bool 均拒绝；
- `precursor_mz` 必须是有限、非负数；
- `intensity` 必须是有限数；
- isolation window 上下界必须为空或省略。

### 2.2 isolation_window

`precursor_kind` 必须显式为 `isolation_window`。规则为：

- `charge` 必须显式为 JSON `null`；
- `precursor_mz` 与 `intensity` 为 null，不承载 selected-ion 语义；
- `isolation_lower_mz`、`isolation_upper_mz` 必须同时存在；
- 两者必须有限、非负，且 lower 严格小于 upper；
- 不复制 DIA-NN identification，不使用 identification 的 m/z 或 charge。

## 3. m/z 最终语义

DDA 中 `precursor_mz` 仍是 selected precursor m/z，语义不变。
DIA 中权威值是绝对窗口边界；`precursor_mz=null`。窗口中心可由上下界派生，
但不写入 `precursor_mz`，因为中心不是唯一 selected precursor。

现有 mzML 解析层仍可把源文件的 target m/z、上下 offset 保存在
`mzml_metadata` v1 中；核心 DIA 记录使用计算后的绝对下界和上界。两个位置
表达不同层次：Extension 保留源编码，core 表达统一采集窗口合同。

## 4. 旧 DDA 兼容和 canonical JSON

旧 v1/v2 DDA 记录没有 `precursor_kind`。Reader 构造模型时保留这一原始事实，
`effective_precursor_kind` 统一返回 `selected_precursor`，调用方不需要按文件
版本或字段来源分支。

新增三个字段使用“值为 null 时省略”的 dataclass 序列化元数据。旧 DDA
构造路径不设置新字段，因此 `core_precursors` JSON、块 checksum、后续 offset、
顶层目录和完整文件字节均保持不变。显式新 DDA 可以写
`precursor_kind=selected_precursor`；DIA 必须显式写 `isolation_window` 和边界。

## 5. Validator 稳定拒绝

v1 与 v2 Validator 共用同一条件合同，稳定错误码为：

~~~text
INVALID_PRECURSOR_KIND
MISSING_PRECURSOR_CHARGE
INVALID_PRECURSOR_CHARGE
MISSING_ISOLATION_WINDOW
INVALID_ISOLATION_WINDOW
PRECURSOR_KIND_FIELD_CONFLICT
INVALID_PRECURSOR_MZ
INVALID_PRECURSOR_INTENSITY
~~~

Validator 不会忽略非法 charge 后继续成功。旧 DDA 缺 charge、DDA 空/零/负
charge、DIA 非空 charge、缺窗口边界、零宽/反向/非有限窗口都拒绝。

## 6. Writer、物理格式和九块边界

生产写入仍只有 `binary_layer/writer.py` 中的 `ZpWriter`。本阶段没有新增
DIA Writer，Adapter/Tool 不直接写 JSON 或 `.zp`。以下冻结项均不变：

- 24-byte Header、Directory 和 checksum 规则；
- 九块名称、数量与固定顺序；
- v1 JSON arrays 和 v2 `zp-arrays-v2` Header/目录/payload；
- offset、对齐、Writer 版本分派；
- `ZP_VERSION=1`、`DEFAULT_ZP_WRITE_VERSION=1`；
- 空 `core_chromatograms` 和 `extensions` 仍存在。

## 7. 迁移和逻辑指纹

`migrate_v1_to_v2` 对非-arrays 块继续原样复制，仅改
`global_meta.format_version`。因此：

- 旧 DDA v1 迁移后仍按 selected precursor 解释；
- DIA v1 的 kind、null charge 和绝对窗口边界逐字节保留到 v2；
- 缺 kind 与显式 selected 在逻辑指纹中通过删除默认 kind 后等价；
- isolation kind 和窗口边界不被删除，参与逻辑比较，不会与 DDA 混同；
- 旧 DDA 迁移 Golden 的历史逻辑指纹保持不变。

## 8. 真实 DIA 只读证据

对类型 A mzML 的首批三个 MS2 做了只读、停止式抽样。每个 MS2 有一个
precursor 容器，isolation target 分别为 382、394、406 Th，上下 offset 均为
6.5 Th，即 13 Th 窗口。`selectedIon` 重复 target 并带源强度，但没有 charge。
这确认源语义是 isolation window，不是一个带唯一 charge 的 DDA precursor。

抽样未修改真实文件、未向真实目录写入缓存或 `.zp`，未运行完整 Bottom-Up
转换。

## 9. P2-C2 边界

本阶段没有解析 `all_report.parquet`、没有写 110,026 条 identification、没有
实现 quantification、spectral library、Bruker `.d` 或 DIA-NN Adapter。

P2-C2 后续应让 `dia_precursor_identification` 关联 core Spectrum；该 Spectrum
再关联一个 `isolation_window` core precursor。identification 自身的 charge、
precursor m/z 和 peptide/protein/quant 事实进入冻结的 Bottom-Up/DIA Extension，
不得复制为 core acquisition precursor。

## 10. 阶段状态

P2-C1.1 解除了 `dia_ms2_core_precursor_contract_unresolved`：DDA 和 DIA
采集语义可在同一 `core_precursors` 块中表达，同时保持旧 DDA 字节、九块结构
和 v1/v2 物理布局。该结论不表示完整 Bottom-Up 转换、DIA-NN Adapter 或
P2-C2 已完成。
