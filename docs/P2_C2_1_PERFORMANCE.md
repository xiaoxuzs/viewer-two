# P2-C2.1 大型 DIA `.zp` 转换与验证性能整改

## 1. 结论

本阶段没有改变 v1/v2 物理格式、九块结构、块顺序、checksum 算法、
`ZP_VERSION` 或默认写出版本，也没有删除或抽样任何科学门禁。

P2-C2 的状态保持为：

```text
P2-C2功能链路已跑通
P2-C2真实科学数据正确性已验证
P2-C2性能验收未通过
reason=conversion_and_full_validation_performance_unacceptable
```

P2-C2.1 已使日常路径满足性能合同：

| 操作 | 真实耗时 | 目标 | 结论 |
| --- | ---: | ---: | --- |
| open | 0.000021 s | <= 3 s | 达到 |
| Header read | 0.001405 s | <= 3 s | 达到 |
| GlobalMeta summary | 0.001699 s | <= 3 s | 达到 |
| 首次 v2 单数组随机读取 | 2.8407 s | <= 3 s | 达到 |
| 相同 Reader 缓存后随机读取 | 0.000957 s | <= 3 s | 达到 |
| 2.52 GB quick validation + 证书复用 | 6.4527 s | <= 30 s | 达到 |

首次完整转换从 5,445.01 秒降至 645.95 秒，即约 8.43 倍提速，进入
分钟级，但仍不是 30 秒操作。当前 v2 deep physical validation 为
123.19 秒，其中 1.097 GB Extension canonical JSON 的解析和规范化校验
单独需要 101.75 秒。因此结论必须写为：

```text
v2深度业务验证无法满足30秒
需要v3列式Extension
```

## 2. 真实输入与产物身份

正式输入总大小为 1,496,082,385 bytes。性能整改后的完整转换生成：

```text
file_size=2,521,241,519
sha256=c8426c567f9e9f76266c16a27184fa9c0e726c82bebc3d0c9028915a864bd2ac
format_version=2
checked_blocks=9
physical_valid=true
bottom_up_valid=true
```

其大小和 SHA-256 与 P2-C2 正式产物完全相同。源文件转换前后身份也
完全相同。物理格式和旧 Golden 均未改变。

## 3. 优化前后总览

| 阶段 | 优化前 wall | 优化后 wall | 优化后 CPU | 规模/备注 |
| --- | ---: | ---: | ---: | --- |
| inspect | 0.925 s | 0.604 s | 未分离 | 输入总计 1.496 GB |
| mzML parse | 包含在 334.212 s | 220.327 s | 207.984 s | 109,766 Spectrum |
| mzML admission | 0.754 s | 0.451 s | 0.453 s | 全量入门检查 |
| core Block build | 包含在 334.212 s | 14.208 s | 12.531 s | 219,534 Array |
| Parquet parse | 227.104 s | 69.871 s | 66.203 s | 323,232 rows / 40 batches / 69 columns |
| Spectrum association | 3.466 s | 2.217 s | 2.172 s | 110,026 identifications |
| Extension build | 5.649 s | 7.193 s | 6.125 s | 362,929 relationship records |
| Writer | 1,017.542 s | 132.083 s | 124.719 s | 2.521 GB output |
| v2 physical Validator | 3,748.991 s 中主要部分 | 123.187 s | 117.844 s | 九块，全量 |
| Bottom-Up Validator | 未分离 | 4.523 s | 4.391 s | 110,026 source_fields |
| 完整转换总计 | 5,445.008 s | 645.947 s | 607.906 s | 8.43x |
| 进程峰值 RSS | 9,596,948,480 B | 9,971,982,336 B | — | 未改善 |

转换 profiling 的操作系统计数为读取 16,322,401,909 bytes、写入
2,521,241,543 bytes。读取量包含输入身份在转换前后各一次 SHA、源解析、
Writer 后验证和最终输出 SHA；它不是唯一物理磁盘流量，因为操作系统可能
命中页缓存。

## 4. 分阶段证据

下表中的“读/写”优先报告阶段明确消费或产生的逻辑字节数。当前 Windows
profiling API 只提供进程级 peak RSS 和 I/O 累计值，因此无法诚实地把
9.97 GB 全局峰值伪装成每个阶段的独立精确峰值；表中的 RSS 是运行期间
采样值或全局上界。未来 profiler 已保留各阶段 wall/CPU、循环和规模指标。

| 阶段 | wall / CPU | 读 / 写 | 循环与结构规模 | RSS 证据 |
| --- | --- | --- | --- | --- |
| mzML解析 | 220.327 / 207.984 s | 1,445,130,808 B source / 0 | 109,766 spectra；161,457,642 float64 values | 解析/Block 前半段达到全局峰值 9.97 GB |
| Block构建 | 14.208 / 12.531 s | 内存中 1,291,661,136 B numeric payload / 0 | 219,534 arrays | 包含于 9.97 GB 上界 |
| Parquet解析 | 69.871 / 66.203 s | 37,773,200 B primary report / 0 | 323,232 rows，40 batches，69 columns | 包含于 9.97 GB 上界 |
| Spectrum关联 | 2.217 / 2.172 s | 内存 / 0 | 110,026 queries；53,110 distinct MS2 | 包含于 9.97 GB 上界 |
| Extension构建 | 7.193 / 6.125 s | 内存 / 0 | 362,929 relationship records | 包含于 9.97 GB 上界 |
| Extension JSON序列化 | 87.695 / Writer CPU内 | 内存 / 1,096,558,285 B | 流式 canonical batches | Writer 阶段采样约 3.18–5.47 GB |
| 数组目录和checksum第一遍 | 18.846 / Writer CPU内 | 1,291,661,136 B memory / directory bytes | 219,534 arrays；219,534 loops | Writer 阶段采样约 3.18–5.47 GB |
| payload写入第二遍 | 20.086 / Writer CPU内 | 1,291,661,136 B memory / 1,291,661,136 B file | 219,534 arrays；219,534 loops | Writer 阶段采样约 3.18–5.47 GB |
| 文件级checksum | 4.750 / 未分离 | 2,521,241,519 B / 0 | 分块 SHA-256 | 低于全局 9.97 GB 上界 |
| v2物理Validator | 123.187 / 117.844 s | 1,229,580,359 B 显式读取 + 1,291,661,136 B mmap访问 / 0 | 13 reads；219,534 numeric chunks | Validator 阶段采样约 6.78 GB；数组不复制全 payload |
| Bottom-Up业务Validator | 4.523 / 4.391 s | 复用已解析 Extension / 0 | 362,929 relations；110,026 source_fields | 同一 deep 进程上界 |
| source_fields检查 | 包含在 4.523 s | 0 额外 JSON I/O / 0 | 110,026 structure/type checks | 不再逐条 `json.dumps` |

## 5. 具体热点与整改

### 5.1 v2 数组 Validator

热点位于 `binary_layer/v2_validator.py::_validate_arrays_region`。原实现用
Python `struct.iter_unpack` 逐个检查约 1.61 亿个值。现实现使用只读 mmap、
`numpy.frombuffer`、`np.isfinite` 和按 `array_type` 的向量化非负检查，同时
对每个数组和整个 arrays 块执行原 SHA-256。

真实数组区包含 219,534 arrays、161,457,642 values 和
1,291,661,136 payload bytes。独立全量扫描为 21.289 秒；5,000,000 值的
同一算法对比为 Python 2.5014 秒、NumPy 0.02083 秒，约 120 倍。没有抽样，
也没有把 1.34 GB arrays 块复制到单个 Python bytes 对象。

### 5.2 Extension JSON

热点位于 `binary_layer/v2_validator.py::_read_json_blocks` 和
`binary_layer/serialization.py::canonical_json_bytes`。真实 Extension 为
1,096,558,285 bytes：UTF-8 decode 0.427 秒、`json.loads` 28.586 秒、
canonical reserialization 70.462 秒，总计 101.747 秒。这是当前 v2 deep
无法达到 30 秒的直接测量证据。

Writer 的 `binary_layer/serialization.py::iter_canonical_json_bytes` 现在按
列表 batch 流式产生与旧实现字节完全相同的 canonical JSON，避免构造第二份
1.097 GB 序列化结果。路线 A 因而适合 v2 写出和 quick 路径，但不能给 v2
Extension 提供真正的随机访问，也不能消除 deep canonical 校验成本。

### 5.3 Bottom-Up Validator

热点位于 `binary_layer/bottom_up_validator.py::_unique_ids` 及关系检查。
`list.count` 的 O(n²) 重复扫描已改为一次字典计数；110,026 个唯一 ID 的
合成测量为 0.138 秒。Extension 只解析一次并由 physical、Top-Down 和
Bottom-Up 共享。`source_fields` 在 Adapter/Writer 入门处确认可序列化，
业务 Validator 只检查结构和标量域，不再对 110,026 记录逐条 canonical
重建。结果从早期 41.307 秒降至 4.523 秒，科学错误码保持不变。

### 5.4 StringPool 和关系

热点位于 `binary_layer/v2_validator.py::_validate_string_pool`。原来的
`value in list` 对 109,768 references 形成 O(n²)；现在先建立 set，再线性
检查。对应合成测量为 0.051 秒。当前真实关系校验为 0.714 秒。

### 5.5 Writer 两遍数组扫描

热点位于 `binary_layer/v2_arrays_writer.py::prepare_v2_arrays_layout` 和
`write_v2_arrays_block`。v2 把带每数组 checksum/offset/length 的目录放在
payload 之前，因此不改变物理布局时，目录 checksum 必须先于 payload 已知。
Writer 仍需两遍 array-record 扫描，但不再逐个执行 Python `struct.pack`；
可信 mzML 数组通过连续 little-endian NumPy buffer 分块 hash/write。缓存全部
编码 payload 可省第二次数值转换，却会额外常驻约 1.29 GB，当前没有采用。

顶层块 checksum 在流式写出时同步生成；没有第二个 Writer，Tool 仍不能写
`.zp`。

### 5.6 转换内存

`ZpWriteStep` 在单一 Writer 成功后立即释放 `PipelineContext.blocks`，使后续
deep Validator 不再与 7–9 GB 源 Block 对象同时常驻。运行中 RSS 从约
9 GB 降到约 3.18 GB 后才进入验证，修复了第一次性能运行在嵌入 deep 时的
内存叠加问题。

但总峰值仍为 9.97 GB，来自 mzML decode 后的 Python float tuple/list 和
Extension 源对象同时驻留。要显著降低峰值，需要把解析器到 Writer 的数组
所有权改为 ndarray/array buffer 或 spool 生命周期；这属于后续内存架构整改，
不能通过提高内存上限解决。

## 6. quick validation 合同

公开接口为：

```python
validate_zp(path, mode="quick", certificate_path=certificate)
validate_zp(path, mode="deep", certificate_path=certificate)
```

quick 严格检查 Header、Directory schema/顺序/编码/边界、九块原始字节
checksum，并在同一顺序扫描中计算完整文件 SHA-256。它不会解析 Extension
JSON，也不会访问任何数组 float64 值。真实结果：

```text
wall_seconds=6.4526645
cpu_seconds=6.0
peak_rss=110,202,880
checked_blocks=9
bytes_read=2,521,252,565
extension_json_parsed=false
array_values_visited=0
certificate_valid=true
deep_validation_reused=true
```

quick 不是科学深度验证。没有证书时，它只报告本次物理完整性和计算出的文件
SHA；只有文件 SHA、文件大小、format version、Directory checksum、九块
checksum 和 Validator 合同全部与证书一致时，才复用 deep 结论。

## 7. deep 验收证书

证书 schema 版本为 1，Validator 合同为 `p2-c2.1-v1`。它确定性绑定：

- 完整 `.zp` SHA-256 和文件大小；
- format version、原始 Directory SHA-256、九块 checksum；
- Validator 合同版本和 Bottom-Up schema version；
- 实体计数、数组数量、数组总值数量；
- physical、Top-Down 和 Bottom-Up deep 结果。

证书不写绝对路径、当前时间、随机 UUID、用户名或临时目录。文件改变一个字节
会导致 `DEEP_VALIDATION_CERTIFICATE_FILE_MISMATCH`；合同版本不兼容会导致
`DEEP_VALIDATION_CERTIFICATE_VERSION_INCOMPATIBLE`。

## 8. 检查点与恢复

`scripts/run_dia_result_acceptance.py` 的确定性检查点绑定文件 SHA 和
Validator 合同，记录：

```text
physical_validation_completed
bottom_up_validation_completed
source_array_comparison_completed
association_comparison_completed
reader_verification_completed
```

相同 SHA/合同的重跑跳过已有有效证据；文件 SHA 改变后五个阶段全部自动失效。

## 9. v3 路线

路线 A 已完成：保持 v2，Writer 流式 JSON，deep 共享一次 parse，quick 完全
跳过 Extension parse。路线 A 能满足日常 quick，但不能满足 30 秒 deep 和
Bottom-Up 真随机访问。

路线 B 应设计 v3 列式/二进制 Extension，至少需要独立 schema/version、
列目录、每列 checksum、实体 ID/外键索引、可分块数值列和 canonical metadata。
这会改变物理格式，必须经过独立门禁，并同步更新 constants、models、Writer、
Reader、Validator、迁移、测试和 README；本阶段没有越过该门禁。

## 10. 科学与兼容性结论

- 所有数组仍被全量检查，不是抽样；NaN/Infinity 和负 m/z/time 仍拒绝。
- Bottom-Up 外键、反向关系、计数、排序和数值域仍全量检查。
- quick 与 deep 对合法 v1/v2 文件均通过。
- Header、Directory、块 checksum、证书字节变化和证书版本损坏均有独立测试。
- 旧 v1/v2 Golden 字节和 P1-B7 arrays fixture hash 未改变。
- 物理格式改变：否。
- 科学门禁改变：否。
- 达到：open/summary/random <= 3 秒，quick <= 30 秒，转换显著降至分钟级。
- 未达到：v2 deep <= 30 秒；转换峰值 RSS 未降低；Bottom-Up v2 JSON 实体随机
  访问仍需解析整个 Extension。前两项有上述实测证据，后一项需要 v3。

## 11. 最终门禁结果

```text
定向 quick/deep/Bottom-Up/checkpoint 回归：44 passed
完整损坏矩阵：291 passed
B8.5 release_gate=true
B8.6 release_gate=true
B8.6 total_gate_seconds=136.680228
B8.6 fault injection=33 passed
B8.6 streaming=18 passed
B8.6 cross-implementation independence=2 passed
全量 pytest=1004 passed in 46.75s
```

B8.6 的 31.4 MB 真实样本迁移检查了全部 4,098 个 array hashes；迁移 v2
与直接 v2 字节完全相同，逻辑 fingerprint 相同，源文件未变化。生产源码
冻结和旧模块冻结均为 true。旧 P1-B7 Golden arrays bytes/hash 保持不变。

最终全量 pytest 已通过；`git diff --check` 在交付前执行。v2 deep 超过
30 秒和 9.97 GB 转换峰值不因测试门禁通过而消失，仍按本报告列为明确
未达目标。
