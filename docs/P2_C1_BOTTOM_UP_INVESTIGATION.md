# P2-C1 Viewer Bottom-Up 输入调查、真实数据分类与 .zp Schema 冻结

本文是只读调查与设计冻结报告。事实来源是 Viewer 代码、独立 .zp 项目当前格式约束，以及本机调查根目录 D:\dia-shuju 中的真实文件。本文没有把目录名、扩展名或说明文档当成业务类型结论；所有结论均由代码入口、文件头/Schema、首条合法记录、实体计数或关联验证支持。

## 1. 阶段结论

Viewer 当前 Bottom-Up 主链路已经唯一确认：

~~~text
DIA-NN 2.0 all_report.parquet（缺失时回退 target_report.parquet）
+ 至少一个 mzML、Thermo RAW 或有效 Bruker .d 谱图来源
+ 可选同名前缀 stats/protein_description
+ 可选 PFMB sidecar，或由唯一 *.pos.pkl 派生 PFMB sidecar
~~~

它不是 pFind、PFMB 主结果包、mzIdentML、pepXML、MGF、CSV 或自定义 JSON 链路。PFMB 是可降级缺失的预计算碎片证据侧车，不是主鉴定输入。

D:\dia-shuju 的两类业务数据是：

- 数据类型 A：单个 Thermo Q Exactive HF-X DIA mzML 对应的 DIA-NN 2.0 Bottom-Up 鉴定、前体/蛋白组定量、谱图库及可选 pos/infoneg 中间结果；它与 Viewer 当前 DIA-NN 链路匹配。
- 数据类型 B：单个 Bruker timsTOF Pro DIA-PASEF TDF 原始 run；它没有对应 DIA-NN 报告、PSM/肽段/蛋白结果，单独不能作为 Viewer Bottom-Up 结果包导入。

逻辑 Bottom-Up Extension 草案、ID、Admission、Reader 和业务 Validator 规则已在本报告中冻结为条件合同。但是当前不能进入 P2-C2 生产实现，原因不是 Viewer 合同未确认，而是当前冻结的 v1/v2 core 模型无法忠实表达真实 DIA MS2：

- 真实 mzML 的 103,988 个 MS2 均没有选定前体 charge；
- 当前 ZpValidator 要求每个 MS2 必须双向唯一引用一个 core_precursors 记录；
- core_precursors.charge 是必需整数；
- 一个 DIA 窗口谱图实际关联多个不同电荷的 DIA-NN 前体，不能从某条鉴定记录复制 charge；
- 使用 -1、0、1 或任意鉴定 charge 都是被项目约束明确禁止的哨兵或伪造业务事实。

因此完整模式的 Admission 结论是稳定拒绝，阻塞码为 DIA_MS2_CORE_PRECURSOR_UNREPRESENTABLE。不得通过放松 Validator、伪造 charge、把 MS2 伪装为 MS1 或省略全部 MS2 来绕过。

## 2. 两个仓库前置状态

调查前及新增本报告前记录如下。

| 仓库 | branch | HEAD | 已暂存 | 工作区 |
|---|---|---|---|---|
| E:\viewer | main | 6417a642aeefdef7e01eff8c04f3c19606c5ec12 | 无 | mzml-demo/scripts/prsmup.py 已修改；mzml-demo/tests/ 未跟踪 |
| E:\viewer-two | main | b018531c417c1f7f4d789510034a270790a9004c | 无 | 22 个 tracked 文件有既有修改，另有 P1/P2 Writer/Reader/迁移/Top-Down/测试等大量未跟踪文件 |

E:\viewer 的 diff stat 是 1 file changed, 10 insertions(+), 7 deletions(-)。E:\viewer-two 的 tracked diff stat 是 22 files changed, 1298 insertions(+), 93 deletions(-)。两个仓库的 git diff --check 均返回 0，仅报告既有 LF→CRLF 提示；git diff --cached --stat 均为空。

本阶段没有修改 E:\viewer，没有修改或取消两个仓库中的任何既有修改，没有执行 git add、commit、push、reset、clean、stash、rebase、restore 或 checkout 覆盖。

## 3. Viewer Bottom-Up 调查范围

按要求调查了：

- E:\viewer\back：上传/路径导入、root resolver、planner、DIA-NN reader/adapter、run discovery、数据库真源、BU API/service、mzML scan resolver、Bruker TDF、PFMB。
- E:\viewer\front：导入弹窗、上传文件保持、bu-viewer 页面、DTO、API client、列表/详情/谱图/PFMB/定量展示。
- E:\viewer\docs 与 E:\viewer\说明文档：开发说明、导入中间层、BU/PFMB 语义说明。
- E:\viewer\shuju 及仓库内交付样例：仅作为代码行为和 PFMB 格式的辅助证据，不代替 D:\dia-shuju 真实文件。

搜索范围覆盖 Bottom-Up/BottomUp/bottom_up/BU、PFMB、pFind/pLabel/pBuild/pQuant、DIA-NN/diann、PSM/peptide/protein/fragment/annotation、q-value/FDR、mzIdentML/pepXML/idXML、MGF/mzML、TSV/CSV/JSON 等关键词。找到的唯一生产 BU 主适配器是 universal_diann_adapter.py。

主要代码证据：

| 事实 | 文件与位置 |
|---|---|
| 上传 UI 的 DIA_NN 类型 | front/src/features/import-upload/ImportUploadDialog.tsx:83 |
| 文件/目录选择与 relativePath | front/src/features/import-upload/ImportUploadDialog.tsx:581；importUploadFiles.ts:42 |
| 上传类型必须匹配 DIANN_DIA plan | back/app/import_uploads/dispatch.py:19 |
| 服务器路径导入是目录 | back/app/api/v1/imports.py:61；back/app/schemas/imports.py:10 |
| BU root 判定 | back/app/dataset_ingest_root/resolver.py:37 |
| BU planner | back/app/services/import_planner/planner.py:61 |
| 谱图类型 mzML/TDF/mixed | back/app/services/import_planner/detectors.py:41 |
| DIA-NN 列与过滤 | back/app/ingest/bu/diann_parquet_reader.py:15 |
| BU 生产适配器 | back/app/ingest/bu/universal_diann_adapter.py:120 |
| run 发现与配对 | back/app/ingest/bu/run_discovery.py:43 |
| 数据库真源 | docs/universal_schema.sql |
| Pydantic DTO | back/app/schemas/bu.py |
| BU 前端 | front/src/features/bu-viewer |

## 4. Viewer 当前导入入口和执行链路

### 4.1 浏览器本地上传入口

ImportUploadDialog 提供 DIA-NN result folder。DIA_NN 没有单文件扩展名过滤；用户选择目录后，浏览器上传目录内所有文件并保留 webkitRelativePath。前端创建 upload session，逐文件 PUT relative_path，最后 POST start；start 参数是 slug、name、可选 description，不允许前端传 source_path。

~~~text
选择本地目录
→ POST /api/v1/import-uploads
→ PUT /api/v1/import-uploads/{upload_id}/files?relative_path=...
→ POST /api/v1/import-uploads/{upload_id}/start
→ resolve_ingest_root
→ plan_zip_ingest
→ plan.shape 必须为 DIANN_DIA
→ 既有 path-import job
~~~

### 4.2 服务器路径入口

POST /api/v1/imports 接受 source_path、slug、name、description。source_path 必须存在且是目录，后端调用 resolve_ingest_root。它不是要求用户点选某个 parquet 的单文件 API。

### 4.3 root 识别

has_bu_diann_layout 要求递归找到名字严格为 all_report.parquet 或 target_report.parquet 的报告，并且同时找到 mzML、Thermo .raw 或任意 .d 目录。find_ingest_root 接受当前目录，或唯一一个直接子目录；多个候选数据集目录稳定拒绝。BU 比弱的 mzML-only 信号优先。

### 4.4 后端执行链路

~~~text
输入目录
→ resolve_ingest_root
→ plan_zip_ingest(shape=DIANN_DIA)
→ find_diann_report（all 优先、target 回退）
→ inspect_report 获取 DIA-NN Run 集合
→ discover_bu_runs 发现 mzML/RAW 转换结果/有效 .d
→ normalize_diann_run_name + match_diann_runs_to_files
→ iter_filtered_rows（非 decoy 且 Q.Value < 0.01）
→ _collect_entities_and_matches
→ PostgreSQL datasets/runs/proteins/peptides/
  protein_relation_mapping/identification_matches
→ BU API/service
→ front/src/features/bu-viewer
~~~

Viewer 当前 adapter 将过滤后的行一次性 list 化；Parquet reader 本身用 iter_batches。数据库只保存 metadata、文件路径、关系与查询字段；峰数组仍从 mzML/TDF/PFMB 读取。

## 5. Viewer 要求的输入文件角色

| 角色 | 是否必需 | 文件名/匹配规则 | 格式 | 用途 | 缺失行为 |
|---|---|---|---|---|---|
| 谱图来源 | 必需 | 递归 *.mzML/*.mzml，Thermo *.raw，或有效 *.d | mzML/RAW/TDF | run、谱图、XIC、色谱/DIA 窗口 | 无 run：adapter 抛错 |
| PSM/鉴定结果 | 必需 | all_report.parquet 优先；否则 target_report.parquet | DIA-NN 2.0 Parquet | 实际为 DIA precursor identification，不是源生 scan PSM | resolver 不识别 |
| Peptide 结果 | 自动推导 | Stripped.Sequence/Modified.Sequence | 同主报告 | peptide 与 peptidoform 显示 | 空序列当前 Viewer 跳过 |
| Protein 结果 | 自动推导 | Protein.Group；Protein.Ids 只作 metadata | 同主报告 | protein 与 peptide 关系 | 可为空，关系缺失 |
| Protein group | 自动推导 | Protein.Group，分号分隔成员 | 同主报告 | group、PG q、PG MaxLFQ | 可为空 |
| Modification | 自动推导 | Modified.Sequence 中 UniMod token | 同主报告 | 仅字符串显示 | 无独立表/定位模型 |
| Fragment 结果 | 可选 | results.pfmb + index.json；或唯一 *.pos.pkl 可派生 | PFMB/JSON/PKL | 预计算 slot fragment evidence | 缺失/生成失败时 BU 仍成功 |
| FASTA | 可选 | 目录内可唯一解析的 FASTA | FASTA | protein sequence/coverage | list_only/partial coverage |
| 定量结果 | 主表部分必需；矩阵可选 | Precursor.Quantity、PG.MaxLFQ；*.pr_matrix.tsv/*.pg_matrix.tsv | Parquet/TSV | 前体强度、蛋白组 MaxLFQ | 主字段可空；矩阵当前不是必需导入角色 |
| 搜索参数 | 可选 | *.log.txt、manifest | 文本/逐行 JSON | 软件/参数 provenance | 当前主 adapter 不依赖 |
| 谱图库 | Viewer 未使用 | all_lib.parquet/target_lib.parquet | DIA-NN Parquet | 参考碎片谱图库 | 当前链路忽略 |
| MGF | 不支持 | 无发现规则 | MGF | 无 adapter | 不会成为 DIANN_DIA |
| mzIdentML/pepXML/idXML | 不支持 | 无发现规则 | XML | 无 parser | 不会成为 DIANN_DIA |

Viewer 一个目录可以包含多个 run；report 的每个 Run 必须经去路径、去 .mzml.gz/.mzml/.raw/.d、转小写后匹配谱图文件名。只有一个已发现 run 时，Viewer 允许名字不匹配的单 run fallback；多个 run 时所有 report Run 都必须匹配。未被 report 引用的额外谱图 run 仍可注册为零 match run。

## 6. Viewer 数据库、API 和前端字段

### 6.1 数据库

Viewer 没有 SQLAlchemy ORM 模型层；docs/universal_schema.sql 是表结构真源，service 使用 SQLAlchemy Session + raw SQL。

| 表 | 核心字段、类型与可空性 | 主外键 |
|---|---|---|
| datasets | dataset_id bigint PK；name/slug/analysis_mode/source_software/source_root/status 非空；description 可空；capabilities/extra_metadata jsonb 非空 | PK dataset_id；slug unique |
| runs | run_id/dataset_id/file_path/file_name/analysis_mode/status 非空；software 可空；三类 metadata jsonb 非空 | FK dataset_id |
| identification_matches | match_id/dataset_id/run_id/scan_number/ms_level/entity_type/entity_id 非空；native_id、RT、mass、mz、charge、intensity、score/e/q/PEP 可空；decoy 非空；extra jsonb | FK dataset/run；entity_id 是多态值，没有 SQL FK 到 peptide |
| peptides | peptide_id/dataset_id/sequence 非空；mass/length/missed 可空；extra jsonb | FK dataset；unique(dataset,sequence) |
| proteins | protein_id/dataset_id/accession/is_decoy 非空；gene/description/sequence 可空；extra jsonb | FK dataset；unique(dataset,accession,is_decoy) |
| protein_relation_mapping | mapping/dataset/protein/entity_type/entity_id/is_unique 非空；start/end 可空；extra jsonb | FK dataset/protein |

没有 Bottom-Up modification、fragment match、protein_group、feature 或 quantification 独立表。Protein.Group 与 PG 指标存在 extra_metadata；protein group 的 API count 是查询派生，不是独立推断实体。Viewer adapter 写入 scan_number=-1；该实现细节不得进入 .zp。

### 6.2 API

实际 BU API 包括：

- overview、RT-mz heatmap；
- proteins 列表/详情；
- peptides 列表/详情；
- matches 列表/详情；
- match precursor XIC、MS1、MS2、product XIC、mobility slice；
- run chromatogram、DIA windows；
- PFMB slot、annotation、matrix。

Pydantic 输出字段集中在 back/app/schemas/bu.py:11-445。API 列表/详情保留 run、sequence/modified_sequence、mz、charge、RT、mass、q、score、intensity、scan、protein group/accession/gene、PG MaxLFQ/PG q、coverage、DIA-NN extra、谱图数组和 PFMB 字段。

### 6.3 前端真实使用

| 页面/组件 | Viewer required 字段 |
|---|---|
| Overview | counts、q cutoff、run_id/file_name/raw_format/diann_run_name/match_count、capabilities、QC、RT-mz、TIC/BPC、DIA windows |
| Matches 列表 | modified/stripped sequence、run、protein group、precursor m/z、charge、RT、Q.Value、intensity、match id |
| Match 详情 | sequence/modified sequence、run、charge、m/z、q、identification RT、RT start/stop、scan 可用性、protein accessions |
| Peptide | sequence、example modified、length、theoretical mass、missed cleavages、genes、protein/match counts、best q/mz/charge/match |
| Protein | accession、gene、description、group、PG MaxLFQ、PG q、peptide/match count、best q、FASTA sequence、coverage |
| Live 谱图 | core scan/native id/RT/mz/intensity；用 stripped sequence 现场计算 b/y、charge 1/2 |
| PFMB | prsm_index、slot/index/RT、peptide、b/y/c/z_dot、ordinal、charge、理论/实测中性质量、ppm/Da、intensity、PFMB-local peak_id |

Modified.Sequence 的 UniMod 标签由 generated Unimod 字典显示；UniMod:4 显示为 Carbamidomethyl。Live b/y 是运行时推导结果，不是 DIA-NN 文件中的 fragment annotation。

## 7. D:\dia-shuju 目录结构

完整只读枚举得到 3,652 个文件、9,619,791,214 bytes。其中 chakan 目录有 3,540 个工具文件、512,059,135 bytes；demo/docs/plots 是代码、说明和派生图片，不属于两类业务数据。

~~~text
D:\dia-shuju
├─ 20200110_Hela_500ng_DIA_25cm_120min_R1.mzML
├─ DIANN_2.0
│  └─ DIANN_2.0
│     ├─ all_report.parquet / target_report.parquet
│     ├─ all_lib.parquet / target_lib.parquet
│     ├─ *.stats.tsv / *.pg_matrix.tsv / *.pr_matrix.tsv
│     ├─ *.protein_description.tsv / gene matrices
│     ├─ *.log.txt / *.manifest.txt
│     ├─ *.mzML.pos.pkl
│     └─ *.mzML.infoneg.pkl
├─ reference
│  └─ uniprot_human.fasta
├─ DC2817_..._13560.d
│  ├─ analysis.tdf                         (0-byte wrapper marker)
│  └─ DC2817_..._13560.d
│     ├─ analysis.tdf
│     ├─ analysis.tdf_bin
│     ├─ chromatography-data*.sqlite
│     ├─ SampleInfo.xml
│     └─ 13560.m/...
├─ bottom up.zip / DIANN_2.0.zip / *.d.zip (已解压内容的归档副本)
├─ chakan / demo / docs / plots             (工具、说明、派生结果)
└─ README.md / plot*.py / view_d.py
~~~

排除 chakan/demo/docs/plots 后，主要扩展名统计：

| 扩展名 | 数量 | 大小范围 bytes | 说明 |
|---|---:|---:|---|
| .tsv | 12 | 135–8,775,195 | DIA-NN stats/matrix/description |
| .parquet | 4 | 12,951,767–49,967,153 | report/library |
| .pkl | 2 | 411,508,886–565,797,846 | pos/infoneg |
| .mzml | 1 | 1,445,130,808 | Thermo DIA run |
| .tdf | 2 | 0–34,873,344 | wrapper + inner SQLite metadata |
| .tdf_bin | 1 | 1,657,217,024 | Bruker peak binary |
| .fasta | 1 | 13,668,702 | UniProt human |
| .zip | 3 | 448,239,306–2,750,400,909 | 归档副本，不是第三类数据 |

代表性输入身份：

| 相对角色 | bytes | SHA-256 |
|---|---:|---|
| 20200110_Hela_500ng_DIA_25cm_120min_R1.mzML | 1,445,130,808 | 01cfecb120d75c5fd50fcc37e61745cc6bd7301441f12cebc88941e82fe318fa |
| DIANN_2.0/DIANN_2.0/all_report.parquet | 37,773,200 | 9f77a33d182cdef7fdacb32ddc0e85fba631ce828f473f29e707ed334ed6667b |
| DIANN_2.0/DIANN_2.0/target_report.parquet | 12,951,767 | 75c618676bb1a436e45a9b0577e458c8be2010bc5ed0cf4a9b9061333e0d0cfd |
| DIANN_2.0/DIANN_2.0/all_lib.parquet | 49,967,153 | 2484fbb9369e274367f452036303bfec289e49a7fa5a19597e7fb9738e3e2a2a |
| DIANN_2.0/DIANN_2.0/target_lib.parquet | 17,787,591 | cf17f303a92a623002c5bc110a94dc8d0348b1520001c949b84a6d736a97ef7c |
| DIANN_2.0/DIANN_2.0/*.pos.pkl | 565,797,846 | a0f74c9325d3ca5969747d3fbd41c06f0c65e8bb3ac890d51342cd3180855843 |
| DIANN_2.0/DIANN_2.0/*.infoneg.pkl | 411,508,886 | 004f202d316e2ae6d198cf6b8d00947527bbdf76f296a2517c1323ceed448909 |
| .d inner/analysis.tdf | 34,873,344 | 85d9d0252c4bccb05b58c3bb3450200f82f7fd1f93b9b114b37c0d5b0031a594 |
| .d inner/analysis.tdf_bin | 1,657,217,024 | 30b96a1c2647c7bb0c67d34737144feb68cac6c7bb0ef10521a34e31e1f91128 |
| reference/uniprot_human.fasta | 13,668,702 | 477ca5fcad16912bf6c27a8cd7abfbdb5902449858819331213414ecca51c91e |

正式 provenance 只能保存上述相对角色、文件名、大小和 hash；不得保存 TSV/matrix/manifest 中出现的源机器绝对路径。

## 8. 数据类型 A 分析

数据类型 A 是 DIA Bottom-Up 混合结果包：Thermo DIA mzML + DIA-NN 2.0 identification/quantification/library + 可选 PFMB 前置 pickle + FASTA。

| 能力 | 事实 |
|---|---|
| run | 1 个；DIA-NN Run 与 mzML basename 精确归一化匹配 |
| mzML | 有，indexed mzML 1.1.0，Q Exactive HF-X，源为 Thermo RAW |
| MGF/RAW | 无 MGF；没有原始 .raw 文件 |
| PSM | 无源生 scan PSM；有 DIA-NN precursor identification |
| Peptide | Stripped.Sequence/Modified.Sequence |
| Protein/group | Protein.Group/Protein.Ids；PG q/MaxLFQ |
| Modification | 当前通过 Modified.Sequence 的 UniMod:4 |
| Fragment | pos.pkl 可派生 PFMB；library 是参考碎片，不是实验峰匹配 |
| Q/FDR | Q.Value、Global/Lib/Peptidoform/PG/Protein q 与 PEP |
| Quant | precursor/MS1/PG/gene 数值及 pr/pg matrix |
| FASTA | 有，20,432 条 UniProt 序列 |
| 搜索参数 | DIA-NN 2.0 log/manifest |

### 8.1 all_report

- Parquet，顶层 69 列，323,232 行，2 个 row group。
- 一个 Run；323,185 个唯一 Precursor.Id，说明完整表中有 47 个重复 ID。
- Viewer 过滤后 q<0.01 且 non-decoy：110,026 行；110,026 个 Precursor.Id 全部唯一；92,704 个 stripped peptide；8,063 个非空 protein group。
- 有效行的 sequence、charge、Precursor.Id、Run、Protein.Group 均无空值；charge 取值 1–4。
- Parquet null_count 均为 0；语义缺失主要编码为 empty string 或 0，不可把所有 0 自动改为 null。
- RT 单位是 minute；m/z 是 Th；布尔 Decoy/Proteotypic 用 int64 0/1。

### 8.2 target_report

- 111,127 行；q<0.01 且 non-decoy 后同为 110,026 行。
- 有效 Precursor.Id 集合与 all_report 一致。
- 不是第二类业务数据，而是同一 DIA-NN 处理流程的另一阶段。
- 与 all_report 的有效行在 PG.MaxLFQ、PG.MaxLFQ.Quality、Precursor.Normalised、Precursor.Quantity、Quantity.Quality 上存在差异；不得静默覆盖或无 provenance 合并。

### 8.3 TSV

TSV 是 UTF-8/UTF-8-sig 可读、tab 分隔、有表头、无注释行；空字符串表示缺失，数值用普通或科学计数法。

| 表 | 数据行 | 关键列/语义 |
|---|---:|---|
| all_report.stats.tsv | 1 | 17 列；precursors=110026、proteins=7191、总量/MS1/MS2 信号、FWHM、mass/RT accuracy |
| all_report.pg_matrix.tsv | 7,233 | protein group + 单 run quantity |
| all_report.pr_matrix.tsv | 109,159 | group/protein/peptide/mod/charge/Precursor.Id + 单 run quantity |
| all_report.protein_description.tsv | 20,397 | Protein.Id + name/gene/description/sequence；本文件后四列均空 |
| gene matrices | 0 | 仅表头 |

矩阵 sample 列使用源机器绝对路径作为列名。业务层只可把它解析为与唯一 run 匹配的 sample/run 角色；不得原样写入 .zp provenance。

### 8.4 library

all_lib 有 3,503,095 行、295,317 个 precursor；target_lib 有 1,314,767 行、111,105 个 precursor。27 列包含 Precursor、Protein、Product.Mz、Relative.Intensity、Fragment.Type/Charge/Series.Number/Loss.Type 等。当前真实值只见 b/y、charge 1/2、noloss。

这是 DIA spectral library，不是 experimental fragment match。Viewer 当前 adapter 不读取它；首版不能把它错误映射到 bottom_up_fragment_matches。

### 8.5 mzML

- spectrum 109,766：MS1 5,778；MS2 103,988。
- scan/native id 唯一且 scan 连续 1–109,766，native id 形如 controllerType=0 controllerNumber=1 scan=N。
- RT 已按声明单位验证，可归一为 0.123905832–9000.093 seconds。
- 峰数：MS1 14,678,230；MS2 65,940,825；m/z/intensity 均 float64、no compression、等长。
- 54 个 DIA isolation window；典型宽度 13 Th。
- chromatogram 3 条：TIC 与两条 pump pressure；没有源生 BPC。
- 所有 MS2 有 isolation/selected m/z 和前一谱图引用，但全部缺 charge；它们是 DIA window 谱图，不是单一选定前体谱图。

### 8.6 FASTA

FASTA 为 UTF-8，20,432 个唯一 accession，序列长度 2–34,350 aa。有效 report 的分号拆分 accession 共 8,145 个，FASTA 命中 8,144 个；一个 accession 缺失只影响 sequence/coverage，不应删除 protein 鉴定。

### 8.7 pos/infoneg

两文件是 Python pickle protocol 4，顶层包含 list/dict/numpy 对象。调查仅检查 pickle opcode 和结构信号，没有对不受信 pickle 执行反序列化。生产实现不得直接 pickle.load；必须使用可信 PFMB bridge/安全转换，或仅把它作为带 hash 的 preserve-only source_artifact 并明确未嵌入。

## 9. 数据类型 B 分析

数据类型 B 是 Bruker timsTOF Pro DIA-PASEF 原始 TDF run，不是完整 Bottom-Up 结果包。

外层 .d 的 analysis.tdf 是 0 bytes；Viewer resolve_bruker_tdf_root 会进入同名内层 .d。内层：

- analysis.tdf 是 SQLite metadata；
- analysis.tdf_bin 是厂商峰二进制；
- Frames 11,867，ID 1–11,867，时间 0.713333–1259.918184 seconds；
- MS1 frame 913；DIA-PASEF frame 10,954；没有 DDA FrameMsMsInfo 和 PRM 证据；
- 每 frame 927 mobility scans；
- DiaFrameMsMsInfo 10,954 行，12 个 window group、24 个 mobility-window 记录；
- 峰计数 metadata：MS1 225,183,920；DIA frame 412,814,435；
- instrument 为 timsTOF Pro，m/z 100–1700，1/K0 0.6–1.6；
- SampleInfo.xml 是 UTF-16、根标签 SampleTable、无 namespace，含 sample/method/源机器路径等，路径必须脱敏。

它包含可靠 run/frame/isolation/mobility metadata，但当前调查环境没有把 analysis.tdf_bin 解码为 .zp arrays 的生产依赖。它没有 mzML、MGF、PSM、Peptide、Protein、Modification、fragment annotation、q/FDR、鉴定定量、FASTA 或与本 run 关联的 DIA-NN search result。

Viewer 把 D:\dia-shuju 整体识别为 mixed：A 的 report 映射 A 的 mzML；B 被注册成零 match 的额外 run。B 单独目录不满足 has_bu_diann_layout，不能直接走 BU 链路。未来至少需要 Bruker TDF core adapter，以及一个 Run 值能与该 .d basename 匹配的 DIA-NN 报告；若只支持 raw acquisition，则应是未来 DIA raw source，不是 Bottom-Up result adapter。

## 10. 两种数据与 Viewer 合同匹配矩阵

| Viewer 要求 | 数据类型 A | 数据类型 B |
|---|---|---|
| 谱图文件 | 有，匹配 mzML | 有，原始 .d/TDF |
| 主鉴定报告 | all/target_report.parquet | 无 |
| Peptide | 有 | 无 |
| Protein/group | 有 | 无 |
| Modification | UniMod:4 | 无 |
| Fragment annotation | 可由 pos.pkl→PFMB；非必需 | 无 |
| FASTA | 有 | 无 |
| 定量 | report + pr/pg matrix | 只有 acquisition signal，不是鉴定定量 |
| 文件命名 | report Run 精确匹配 mzML basename | 不匹配唯一 report Run |
| 关联键 | normalized Run + RT + precursor m/z/isolation | frame/window，无鉴定键 |
| 当前 Viewer 直接导入 | 是 | 单独否；混合根中仅作零 match run |

明确结论：Viewer 当前使用的是数据类型 A。Viewer 也能在同一 dataset 注册数据类型 B 的 raw run，但这不等于 B 是可直接导入的 Bottom-Up 结果类型。

## 11. 实际应支持的数据类型判定

推荐 SourceInspector source_type：

~~~text
real_dia_result_bundle
~~~

理由：

- 当前唯一真实合同是 DIA-NN DIA precursor identification + DIA acquisition + quantification；
- 直接命名 real_bottom_up_bundle 会掩盖 DIA MS2、window、precursor-level identification 与 DDA scan PSM 的差异；
- Bottom-Up biological entities 仍进入统一 bottom_up_* Extension；
- adapter flavor 在 metadata 中记录 diann_2_parquet，不创建 convert_diann_to_zp 顶层入口；
- 未来 DDA 搜索结果可单独增加 real_bottom_up_identification_bundle adapter，但仍产出相同的业务实体合同；
- B 的 raw-only 类型暂不注册；未来若实现应是 real_dia_raw_run 或既有 raw/mzML source 的 DIA 能力扩展。

所有生产调用仍必须是 convert_source_to_zp(...)。

## 12. Spectrum 关联和 run 边界

### 12.1 Viewer 当前关联

DIA-NN report 没有 scan、native_id 或 rank。Viewer 数据库先写 scan_number=-1，随后在请求谱图时按 identification RT apex + precursor m/z，在 0.5 minute 内寻找 isolation window 包含该 m/z 的最近 MS2。MS1 用 RT 最近邻。这个 -1 是 Viewer 内部兼容哨兵，不得写入 .zp。

对 A 的 110,026 个 Viewer 有效 identification 复现相同规则：

- 110,026/110,026 均能找到 MS2；
- 映射到 53,110 个不同 MS2；
- RT 差中位数约 1.449e-6 minute，95 分位约 5.78e-6 minute，最大 2.2329e-5 minute；
- 30,094 个 MS2 对应多个 identification，单个 MS2 最多 12 个；
- 这证明可构造稳定派生关联，但不把它升级为源生 PSM→scan 事实。

未来可表达时，identification 必须记录：

~~~text
spectrum_id
association_kind = derived_nearest_dia_window
association_rt_delta_seconds
association_precursor_mz
source_scan = null
source_native_id = null
rank = null
~~~

若最近候选不唯一、超容差或 isolation window 不包含 precursor m/z，稳定拒绝；不得任选。

### 12.2 .zp run 边界

Viewer dataset 可多 run；.zp 固定一个文件一个 run。当前 A 是单 run，可生成一个 .zp。未来多 run bundle 对单 target 调用必须返回 MULTIPLE_RUNS_REQUIRE_SPLIT；调用方可用显式相对路径 manifest/run selection 对每个 run 重复调用同一个 convert_source_to_zp，不得由 PipelineRunner 按业务概念分支。

### 12.3 MGF-only/result-only

- MGF 当前 Viewer 不支持，且通常缺 MS1/色谱；P2-C2 完整模式稳定拒绝 MGF_ONLY_UNSUPPORTED。
- 只有结果表、没有谱图：完整模式稳定拒绝 MISSING_SPECTRUM_SOURCE。
- 只有谱图、没有鉴定：不是 real_dia_result_bundle；可交给既有 spectra source，但当前 DIA mzML admission 本身仍拒绝 DIA。
- result-only 或 MS2-only 可作为未来显式降级 profile；不得冒充完整 run，也不得让 identification 引用不存在的 core spectrum。

## 13. PSM、Peptide、Protein 实体关系

当前来源不是经典 DDA PSM，推荐统一实体名 identification，并带 identification_kind=dia_precursor。Reader 可提供 PSM 兼容查询，但必须返回 kind，不能声称源文件有 scan PSM。

| 实体 | 当前真实字段 | 关系 |
|---|---|---|
| Identification | Run、Precursor.Id、modified/stripped sequence、charge、m/z、RT/window、q/PEP、decoy、quant | → one peptide；→ zero/many proteins through group；→ derived zero/one spectrum |
| Peptide | Stripped.Sequence、length | ← many identifications；↔ many proteins |
| Protein | Protein.Group 拆分 accession；可选 gene/description/FASTA sequence | ↔ many peptides；→ group |
| Protein group | Protein.Group 原始有序成员字符串、PG q/MaxLFQ | → one/many proteins；← many identifications |

当前没有真实字段支持 missed_cleavages、is_unique、protein score、coverage、leading protein、shared/unique peptide 明细或 group score。Viewer 的 is_unique 固定 false；coverage 是 FASTA + peptide substring 的派生视图。Schema 字段可为 null/空集合，但不得猜值。

稳定 ID 使用完整 SHA-256，不用数据库自增 ID、随机 UUID 或本地绝对路径：

| ID | 规范化输入 |
|---|---|
| identification_id | run source identity + NUL + Precursor.Id |
| peptide_id | exact stripped sequence |
| protein_id | exact accession + decoy state |
| protein_group_id | exact Protein.Group 成员顺序字符串 |
| modification_id | identification_id + token ordinal + accession + peptide position |
| fragment_id | identification_id + PFMB prsm_index + matched-ion row ordinal |

完整 all_report 中重复的 Precursor.Id 行不全部成为 typed Viewer identification；110,026 个符合 Viewer admission 的 typed identification 唯一。被过滤行及完整原表保存在 source_tables，不静默丢弃。

点名字段逐项结论：

| 实体 | 字段 | 当前真实证据与冻结处理 |
|---|---|---|
| PSM/Identification | psm_id | 无经典 psm_id；Precursor.Id 是 DIA precursor identity，typed ID 按 run+Precursor.Id 派生 |
| PSM/Identification | spectrum_id / scan / native_id | report 均无；只能按 RT+isolation window 派生，不能声称源生 |
| PSM/Identification | peptide_id / protein_ids | peptide_id 由 stripped sequence 派生；Protein.Group/Protein.Ids 提供 protein 关系 |
| PSM/Identification | charge / experimental_mz | Precursor.Charge 与 Precursor.Mz 均存在；分别为 int 和 Th |
| PSM/Identification | calculated_mz | 无独立 calculated m/z |
| PSM/Identification | mass_error / mass_error_ppm | 无；Mass.Evidence 不是 mass error，不得改名 |
| PSM/Identification | retention_time | RT 存在，source minute，typed seconds |
| PSM/Identification | score / score_type | 有多个明确命名的 q/PEP/Evidence；无通用 search score，不能选一个改名 |
| PSM/Identification | q_value / fdr | Q.Value 等 q 存在；无独立 FDR 列 |
| PSM/Identification | rank | 不存在；同一 DIA MS2 可多 identification，但不是 rank 竞争 |
| PSM/Identification | is_decoy | Decoy 0/1，Viewer typed set 仅 non-decoy |
| PSM/Identification | is_unique | 不存在；Proteotypic 不等同 unique |
| PSM/Identification | missed_cleavages | 主 report 不存在；stats 只有平均值，不能回填单条 |
| Peptide | peptide_id / sequence / length | ID 派生；Stripped.Sequence 源生；length 派生 |
| Peptide | modified_sequence / modification_ids | modified sequence 属于 identification/peptidoform；mod IDs 由 token 派生 |
| Peptide | protein_ids / identification_ids | 由 group 与 typed identifications 双向构造 |
| Peptide | best_identification_id / q_value | 可按明确 q 字段确定派生视图；必须标 derived |
| Protein | protein_id / accession | ID 派生；accession 来自 Protein.Group 分号成员 |
| Protein | description / gene | report 目前为空；可选 description TSV/FASTA header，允许 null |
| Protein | sequence / length | 可选 FASTA；length 由 sequence 派生 |
| Protein | peptide_ids / unique_peptide_ids / identification_ids | peptide/identification 关系可派生；unique 语义无证据，保持空/unknown |
| Protein | protein_group_id | Protein.Group 有真实证据，可稳定关联 |
| Protein | score / q_value | 无通用 protein score；Protein.Q.Value 可按原名 typed |
| Protein | coverage | Viewer 从 FASTA+substring 派生，不是 source field |
| Protein | is_decoy | typed Viewer protein 为 false；完整 decoy 行在 source_tables |
| Protein group | leading_protein | 无证据，null |
| Protein group | member_proteins | Protein.Group 分号拆分 |
| Protein group | shared/unique_peptides | 可计算归属数量，但来源没有 inference 标签，首版不宣称 shared/unique |
| Protein group | group_score / group_q_value | 无通用 group score；PG.Q.Value、PG.PEP、Global/Lib PG q 按原名 typed |

## 14. Modification 位置语义

有效 identification 中：

- 唯一 token 是 UniMod:4；
- 21,254 行有修饰；
- 3,863 行有多个修饰；
- 单行最多 6 个；
- token 均紧跟 C，删除 token 后与 Stripped.Sequence 精确相等；
- 位置范围 1–29，均落在 peptide 内；
- 没有 N-terminal、C-terminal、未知质量、定位概率或 variable-mod 证据；
- DIA-NN log 表明 unimod4 是固定 carbamidomethylation。

冻结语义：

~~~text
coordinate_system = peptide_residue_1_based
position = token 前一个 residue 的一基位置
residue = C
accession = UNIMOD:4
name = Carbamidomethyl
mass_shift = +57.021464 Da
is_fixed = true（仅因本包 log 明确）
localization_probability = null
protein_position = null
terminal = none
~~~

同位置多修饰在 v1 结构上通过 token_ordinal 可表达，但当前数据没有证据。未知/terminal token 不得猜位置：无法无歧义解析时记录原 Modified.Sequence 并稳定拒绝 typed modification admission，或留给未来 schema version。位置越界、residue 不一致、同 ID 冲突稳定拒绝。

## 15. Fragment annotation 语义

必须区分三种来源：

1. Live mzML fragment：Viewer 用 stripped sequence 现场计算 b/y、charge 1/2，再与当前原始 MS2 峰做 ppm 最近邻。它是派生视图，不是源文件 fragment annotation。
2. PFMB：预计算 deconvoluted RT-slot fragment evidence。字段为 b/y/c/z_dot、ordinal、charge、intensity、observed/theoretical neutral mass、ppm/Da、PFMB-local peak_id。slot_rt 是 seconds。它不是 live mzML peak。
3. DIA-NN library：b/y、charge 1/2、Product.Mz、Relative.Intensity、noloss；是参考谱图库，不是实验峰匹配。

bottom_up_fragment_matches v1 只接受第 2 类，字段冻结为：

~~~text
fragment_id, identification_id, pfmb_prsm_index, source_row,
slot_index, slot_rt_seconds, ion_type, ordinal, charge,
neutral_loss, theoretical_neutral_mass, observed_neutral_mass,
mass_error_da, mass_error_ppm, intensity, source_peak_id,
peak_space = pfmb_deconvoluted_slot,
spectrum_id = null, core_peak_index = null, source_fields
~~~

当前证据支持 b/y/c/z_dot 与多电荷；没有 a/x、内部碎片、immonium 或中性丢失证据。PFMB peak_id 不能写入 core peak_index。若未来能证明 PFMB 与 core peak 的映射，应新增明确 mapping 字段和校验，不得复用。

## 16. 定量与 DIA 边界

数据类型 A 同时包含 Bottom-Up 鉴定实体和 DIA 定量：

- precursor：Precursor.Quantity/Normalised、MS1 area/normalised/apex、质量与 evidence；
- protein group/gene：TopN、MaxLFQ、quality；
- matrix：单 run 的 precursor 与 protein-group quantity；
- 没有 condition、biological replicate、technical replicate、reporter ion 或稳定 sample display name。

Viewer 当前真实展示 Precursor.Quantity 映射的 intensity 和 PG.MaxLFQ；不展示完整 quant matrix。bottom_up_quantification v1 应是可选但对 A 的 completeness 必须编码的 Extension。measurement 使用强类型枚举、entity_kind/id、run_id、sample_id、value、unit=source_intensity、normalization_kind、quality；condition/replicate 允许 null。matrix 的绝对路径列名只用于匹配唯一 run，不能保存原路径。

DIA isolation windows、ion mobility、DIA spectral library 属于 acquisition/library 语义，不应塞进 peptide/protein quant。后续可评估 dia_acquisition_metadata 或 dia_spectral_library Extension；当前 library 完整表先作为 preserve-only source_table/source_artifact，并明确 Viewer 未使用。

## 17. Viewer 字段覆盖矩阵

标记说明：

- VR：Viewer required，后端实际投影/过滤/持久化或 API/前端依赖。
- SC：Scientific core，强类型进入 .zp。
- OS：Optional supported。
- PO：Preserve only，当前只进入 source_fields/source_tables。
- VB/API/UI：Viewer 后端读取、API 返回、前端真实使用。

所有列来自数据类型 A 的 all_report.parquet。所有 69 个已知列均保留原名/原值；强类型值不能代替原始 source_fields。

| 字段 | Arrow 类型 | 分类 | VB | API | UI | 建议实体/字段 | 必填/缺失策略 | 保留位置 |
|---|---|---|---|---|---|---|---|---|
| Run.Index | int64 | PO | 否 | 否 | 否 | metadata.source_run_index | 可空；0 不自动视为缺失 | source_fields |
| Run | string | VR/SC | 是 | 是 | 是 | metadata.run_name / identification.run_id | 必填 | typed + source_fields |
| Channel | string | PO/OS | 否 | 否 | 否 | quant.channel | 空→null | source_fields |
| Precursor.Id | string | VR/SC | 是 | 是 | 否 | identification.source_precursor_id | typed 行必填 | typed + source_fields |
| Modified.Sequence | string | VR/SC | 是 | 是 | 是 | identification.modified_sequence | 必填且可解析/保留 | typed + source_fields |
| Stripped.Sequence | string | VR/SC | 是 | 是 | 是 | peptide.sequence | 必填 | typed + source_fields |
| Precursor.Charge | int64 | VR/SC | 是 | 是 | 是 | identification.charge | typed 行正整数 | typed + source_fields |
| Precursor.Lib.Index | int64 | SC | 否 | 否 | 否 | identification.library_index | 可空 | typed + source_fields |
| Decoy | int64 | VR/SC | 是 | 是 | 过滤 | identification.is_decoy | 只接受明确 0/1 | typed + source_fields |
| Proteotypic | int64 | SC | 否 | 否 | 否 | identification.is_proteotypic | 0/1；不等同 is_unique | typed + source_fields |
| Precursor.Mz | float | VR/SC | 是 | 是 | 是 | identification.precursor_mz | 有限正数 | typed + source_fields |
| Protein.Ids | string | VR/SC | 是 | extra | 否 | identification.source_protein_ids | 可空 | typed + source_fields |
| Protein.Group | string | VR/SC | 是 | 是 | 是 | protein_group/member relation | 可空→降级无 inference | typed + source_fields |
| Protein.Names | string | VR/SC | 是 | extra | 否 | protein.name | 空→null | typed + source_fields |
| Genes | string | VR/SC | 是 | 是 | 是 | protein.gene | 空→null | typed + source_fields |
| RT | float | VR/SC | 是 | 是 | 是 | identification.rt_seconds | 必填；minute×60 | typed + source_fields |
| iRT | float | SC | 否 | 否 | 否 | identification.irt | 有限 | typed + source_fields |
| Predicted.RT | float | SC | 否 | 否 | 否 | identification.predicted_rt_seconds | minute×60 | typed + source_fields |
| Predicted.iRT | float | SC | 否 | 否 | 否 | identification.predicted_irt | 有限 | typed + source_fields |
| IM | float | OS/SC | 是 | extra | 否 | identification.ion_mobility | 本包全 0；保留不猜缺失 | typed + source_fields |
| iIM | float | SC | 否 | 否 | 否 | identification.iim | 有限 | typed + source_fields |
| Predicted.IM | float | SC | 否 | 否 | 否 | identification.predicted_im | 本包全 0 | typed + source_fields |
| Predicted.iIM | float | SC | 否 | 否 | 否 | identification.predicted_iim | 有限 | typed + source_fields |
| Precursor.Quantity | float | VR/SC | 是 | 是 | 是 | quant.precursor_quantity | 非负；0 保留 | typed + source_fields |
| Precursor.Normalised | float | SC | 否 | 否 | 否 | quant.precursor_normalised | 非负 | typed + source_fields |
| Ms1.Area | float | SC | 否 | 否 | 否 | quant.ms1_area | 非负 | typed + source_fields |
| Ms1.Normalised | float | SC | 否 | 否 | 否 | quant.ms1_normalised | 非负 | typed + source_fields |
| Ms1.Apex.Area | float | SC | 否 | 否 | 否 | quant.ms1_apex_area | 非负 | typed + source_fields |
| Ms1.Apex.Mz.Delta | float | SC | 否 | 否 | 否 | quant.ms1_apex_mz_delta | 有限；单位原样声明 | typed + source_fields |
| Normalisation.Factor | float | SC | 否 | 否 | 否 | quant.normalisation_factor | 有限非负 | typed + source_fields |
| Quantity.Quality | float | SC | 否 | 否 | 否 | quant.quantity_quality | 有限 | typed + source_fields |
| Empirical.Quality | float | SC | 否 | 否 | 否 | quant.empirical_quality | 有限 | typed + source_fields |
| Normalisation.Noise | float | SC | 否 | 否 | 否 | quant.normalisation_noise | 有限 | typed + source_fields |
| Ms1.Profile.Corr | float | SC | 否 | 否 | 否 | quant.ms1_profile_corr | 有限 | typed + source_fields |
| Evidence | float | SC | 读后丢弃 | 否 | 否 | identification.evidence | 有限 | typed + source_fields |
| Mass.Evidence | float | VR/SC | 是 | 是 | 否 | identification.mass_evidence | 有限 | typed + source_fields |
| Channel.Evidence | float | SC | 否 | 否 | 否 | identification.channel_evidence | 有限 | typed + source_fields |
| Ms1.Total.Signal.Before | float | SC | 否 | 否 | 否 | quant.ms1_signal_before | 非负 | typed + source_fields |
| Ms1.Total.Signal.After | float | SC | 否 | 否 | 否 | quant.ms1_signal_after | 非负 | typed + source_fields |
| RT.Start | float | VR/SC | 是 | 是 | 是 | identification.rt_start_seconds | minute×60，≤stop | typed + source_fields |
| RT.Stop | float | VR/SC | 是 | 是 | 是 | identification.rt_stop_seconds | minute×60，≥start | typed + source_fields |
| FWHM | float | SC | 否 | 否 | 否 | identification.fwhm_seconds | minute×60 | typed + source_fields |
| PG.TopN | float | SC | 否 | 否 | 否 | quant.pg_top_n | 非负 | typed + source_fields |
| PG.MaxLFQ | float | VR/SC | 是 | 是 | 是 | protein_group.pg_max_lfq | 非负 | typed + source_fields |
| Genes.TopN | float | SC | 否 | 否 | 否 | quant.genes_top_n | 非负 | typed + source_fields |
| Genes.MaxLFQ | float | SC | 否 | 否 | 否 | quant.genes_max_lfq | 非负 | typed + source_fields |
| Genes.MaxLFQ.Unique | float | SC | 否 | 否 | 否 | quant.genes_max_lfq_unique | 非负 | typed + source_fields |
| PG.MaxLFQ.Quality | float | SC | 否 | 否 | 否 | quant.pg_max_lfq_quality | 有限 | typed + source_fields |
| Genes.MaxLFQ.Quality | float | SC | 否 | 否 | 否 | quant.genes_max_lfq_quality | 有限 | typed + source_fields |
| Genes.MaxLFQ.Unique.Quality | float | SC | 否 | 否 | 否 | quant.genes_unique_quality | 有限 | typed + source_fields |
| Q.Value | float | VR/SC | 是 | 是 | 是 | identification.q_value | [0,1]；Viewer selection <0.01 | typed + source_fields |
| PEP | float | VR/SC | 是 | DB only | 否 | identification.pep | [0,1] | typed + source_fields |
| Global.Q.Value | float | VR/SC | 是 | score | 否 | identification.global_q_value | [0,1]；不命名 generic score | typed + source_fields |
| Lib.Q.Value | float | VR/SC | 是 | 是 | 否 | identification.lib_q_value | [0,1] | typed + source_fields |
| Peptidoform.Q.Value | float | SC | 否 | 否 | 否 | identification.peptidoform_q_value | [0,1] | typed + source_fields |
| Global.Peptidoform.Q.Value | float | SC | 否 | 否 | 否 | identification.global_peptidoform_q | [0,1] | typed + source_fields |
| Lib.Peptidoform.Q.Value | float | SC | 否 | 否 | 否 | identification.lib_peptidoform_q | [0,1] | typed + source_fields |
| PTM.Site.Confidence | float | SC/OS | 否 | 否 | 否 | modification.site_confidence | 本包 0；不冒充定位概率 | typed + source_fields |
| Site.Occupancy.Probabilities | string | OS | 否 | 否 | 否 | modification.site_occupancy | 空→null | typed + source_fields |
| Protein.Sites | string | OS | 否 | 否 | 否 | modification.source_protein_sites | 空→null，不推断坐标 | typed + source_fields |
| Lib.PTM.Site.Confidence | float | SC/OS | 否 | 否 | 否 | modification.lib_site_confidence | [0,1] 或 null | typed + source_fields |
| Translated.Q.Value | float | SC | 否 | 否 | 否 | identification.translated_q_value | [0,1] | typed + source_fields |
| Channel.Q.Value | float | SC | 否 | 否 | 否 | identification.channel_q_value | [0,1] | typed + source_fields |
| PG.Q.Value | float | VR/SC | 是 | 是 | 是 | protein_group.q_value | [0,1] | typed + source_fields |
| PG.PEP | float | SC | 否 | 否 | 否 | protein_group.pep | [0,1] | typed + source_fields |
| GG.Q.Value | float | SC | 否 | 否 | 否 | quant.gene_group_q_value | [0,1] | typed + source_fields |
| Protein.Q.Value | float | SC | 否 | 否 | 否 | protein.q_value | [0,1] | typed + source_fields |
| Global.PG.Q.Value | float | SC | 否 | 否 | 否 | protein_group.global_q_value | [0,1] | typed + source_fields |
| Lib.PG.Q.Value | float | SC | 否 | 否 | 否 | protein_group.lib_q_value | [0,1] | typed + source_fields |

覆盖统计：

- 真实 all_report 列：69。
- Viewer backend REPORT_COLUMNS：25 个名字；当前文件实际存在 24 个，24/69 = 34.78%。不存在的名字是 Ms2.Area。
- Viewer 读后持久化或用于过滤：23/69；Evidence 被读取但未持久化。
- 建议强类型覆盖：69/69 = 100%，其中量化字段进入 typed measurement，不能只放无类型 JSON。
- admitted identification 的 source_fields：69 个原始列；完整 all_report 另进入 source_tables。
- 当前无法解释的 all_report 列：0。
- 未知未来列：不拒绝整个 bundle，进入 source_fields/source_tables，并记录 unknown_columns。

## 18. Bottom-Up Extension Schema 草案

不增加第十个顶层块。extensions 内固定顺序、owner=bottom_up、schema_version=1，建议八个逻辑 Extension 始终存在，空记录也保留：

1. bottom_up_metadata
2. bottom_up_identifications
3. bottom_up_peptides
4. bottom_up_proteins
5. bottom_up_protein_groups
6. bottom_up_modifications
7. bottom_up_fragment_matches
8. bottom_up_quantification

不用 bottom_up_psms 作为当前主名，因为真实 DIA-NN 行不是源生 scan PSM；identification.kind 枚举首版只接受 dia_precursor_identification，未来可增加 dda_psm。

每个 payload 都含 owner、schema_name、整数 schema_version、record_count、按 ID 确定排序的 records。metadata 另含：

- source_type、adapter_flavor、analysis_mode、source_software/version；
- run/core_run_id、原始/归一 run 名；
- Viewer selection policy；
- entity counts；
- spectrum association policy/metrics；
- source_files（relative role/name/size/hash）；
- source_tables（列、参数、完整行或明确分块逻辑）；
- source_artifacts（非表二进制的角色/hash/处理状态）；
- warnings/capabilities。

Typed identification 至少含：

~~~text
identification_id, identification_kind, run_id,
source_precursor_id, spectrum_id, association_kind,
association_rt_delta_seconds, peptide_id, protein_group_id,
modified_sequence, charge, precursor_mz, neutral_mass,
rt_seconds, rt_start_seconds, rt_stop_seconds,
q/PEP 各命名指标, is_decoy, is_proteotypic,
viewer_selected, modification_ids, quantification_ids,
rank=null, source_fields
~~~

source_fields 只承担完整性和未知列，不替代强类型字段。all/target report、quant matrix、library 的角色不能互相覆盖。未知表进入 source_tables；pos/infoneg 等不安全二进制进入 source_artifacts，并记录 typed/preserved_external/rejected 状态。

本 Extension 合同不解决 DIA MS2 的 core_precursors 物理冲突；在该冲突解决前只作为条件冻结草案，不得注册生产 Registry。

## 19. SourceInspector 和输入配对规则

### 19.1 自动识别

real_dia_result_bundle 需要：

- 唯一 primary report candidate：all_report.parquet 优先；不存在时唯一 target_report.parquet；
- primary report 必须有 Run、Precursor.Id、Modified.Sequence、Stripped.Sequence、Precursor.Charge、Precursor.Mz、Q.Value；
- 至少一个 report Run；
- 每个被选择 run 恰好一个归一化名字匹配的 mzML/RAW/.d；
- spectrum 文件可被对应 core adapter admission；
- 一个 convert_source_to_zp target 只允许一个 run。

### 19.2 文件角色

- 同目录 all_report 与 target_report 分别标为 primary_report/refined_report，不是歧义。
- 多个同名 primary report 候选稳定拒绝 AMBIGUOUS_PRIMARY_REPORT，不取字典序第一个。
- mzML 匹配按 basename 去 .mzml.gz/.mzml/.raw/.d 后 casefold；不得只靠“目录里恰好一个文件”fallback。
- FASTA、stats、description、matrix、library、log、manifest、pos/infoneg 都显式发现并分角色。
- zip 是归档副本，若同目录已有解压角色则不重复计为业务输入。

### 19.3 歧义和多 run

- 归一化 run key 冲突：拒绝。
- report Run 无谱图匹配：拒绝。
- 同一谱图匹配多个 Run：拒绝。
- 多 run：拒绝单 target，要求上游显式拆分；一个 .zp 一个 run。
- 额外未引用谱图 run：不像 Viewer 那样静默注册；对单-run .zp 视为 unrelated artifact，记录并拒绝自动选择，除非显式 manifest 排除。

### 19.4 缺少谱图/只有表

完整模式拒绝。未来 result_only 必须是不同显式 source_type/capability，所有 spectrum_id 为 null，Reader 明确告知缺谱；不能由缺文件自动降级。

## 20. Admission Policy 草案

| 情况 | 分类 | 冻结行为 |
|---|---|---|
| 只有 mzML/MGF、无鉴定 | 稳定拒绝本 source_type | MISSING_IDENTIFICATION_RESULT；可由其他 source_type 检查 |
| 只有鉴定、无谱图 | 稳定拒绝 | MISSING_SPECTRUM_SOURCE |
| PSM/identification 无可验证 Spectrum | 稳定拒绝完整模式 | UNRESOLVED_SPECTRUM_REFERENCE |
| 当前 DIA MS2 无 core precursor charge | 稳定拒绝 | DIA_MS2_CORE_PRECURSOR_UNREPRESENTABLE |
| Peptide sequence 空 | 稳定拒绝 typed 行/包 | EMPTY_PEPTIDE_SEQUENCE，不静默 skip |
| Protein accession/group 空 | 降级支持 | 不创建空 protein；identification/peptide 保留，capability=protein_inference_absent |
| Modification 越界/残基不符 | 稳定拒绝 | INVALID_MODIFICATION_POSITION |
| 未知 modification token/terminal 语义 | 未来暂不支持 typed | 原串保留；typed admission 拒绝，不猜位置 |
| Fragment 引用不存在 identification | 稳定拒绝 | INVALID_FRAGMENT_IDENTIFICATION_REFERENCE |
| PFMB peak_id 被声明为 core peak_index | 稳定拒绝 | PEAK_SPACE_MISMATCH |
| Fragment core peak_index 越界 | 稳定拒绝 | INVALID_FRAGMENT_PEAK_INDEX |
| 同一 ID 冲突实体 | 稳定拒绝 | CONFLICTING_ENTITY_ID |
| all/target 原始行数不同 | 允许 | 不要求阶段表相等；分别保存 |
| stats 的 selected count 与 primary 过滤数不一致 | 稳定拒绝或显式 warning | 当前 A 一致；生产默认拒绝，除非 stats 未提供 |
| 多 run 无法唯一拆分 | 稳定拒绝 | MULTIPLE_RUNS_REQUIRE_SPLIT |
| Decoy 语义无法确认 | 稳定拒绝 typed selection | 保存原字段；不能假设 false |
| Score 类型未知 | 保存在 source_fields | typed named score 为 null，不命名 generic score |
| Q-value 缺失 | 降级/Preserve only | 行保留 source_table，不进入 Viewer-selected typed set |
| identification charge 缺失 | 稳定拒绝 Viewer-selected typed 行 | 无 XIC/中性质量能力，不造 charge |
| Protein inference 缺失 | 降级支持 | proteins/groups 为空，鉴定仍在 |
| quant matrix sample 语义不明 | Preserve only | source_tables，typed quant 不猜 sample/condition |
| 未知主表列 | 保存在 source_fields | 不因未知列拒绝 |
| 未知辅助表 | source_tables/source_artifacts | 不静默忽略；不可安全解析时明确 preserve-only/reject |
| MGF-only | 未来版本暂不支持 | 当前完整模式拒绝 |
| result-only | 未来版本暂不支持 | 当前不自动降级 |

## 21. Reader 接口草案

逻辑 Reader 隐藏 v1/v2、Extension JSON 布局、数组 offset 和 parser：

~~~python
get_bottom_up_summary(path)
get_identification(path, identification_id)
get_identifications_for_spectrum(path, spectrum_id)
get_psm(path, psm_id)  # 仅 kind=dda_psm；DIA 记录不得伪装为 PSM
get_peptide(path, peptide_id)
get_protein(path, protein_id)
get_protein_group(path, protein_group_id)
get_modifications_for_identification(path, identification_id)
get_fragment_matches_for_identification(path, identification_id)
get_bottom_up_quantification_summary(path)
get_quantification_for_entity(path, entity_kind, entity_id)
~~~

返回值必须包含 identification_kind 和 spectrum_association_kind。对 DIA identification 的 get_psm 应返回明确类型错误或 NotApplicable，而不是改名包装。

## 22. Validator 规则草案

三层关系：

~~~text
物理 ZpValidator
→ BottomUpExtensionValidator
→ 统一 validate_zp(...) 分开暴露 physical_issues 与 bottom_up_issues
~~~

Bottom-Up 业务验证：

- 八个 schema identity/version/order/record_count；
- identification/peptide/protein/group/modification/fragment/quant ID 唯一且确定排序；
- 所有 run/entity/core spectrum 外键存在；
- identification.kind 与 association 字段组合合法；
- Viewer-selected 行 sequence/charge/mz/RT/q 必需；
- q/FDR/PEP/quality 概率字段在 [0,1]；
- 所有科学数值有限；m/z、charge、intensity/quantity 约束；
- RT seconds 且 start≤apex≤stop；
- PSM/peptide、peptide/protein、protein/group 双向关系一致；
- modification 一基 peptide 坐标合法，residue 与 sequence 相等；
- PFMB fragment peak_space、slot、ion type、ordinal/charge、neutral mass/error 合法；
- core peak_index 只有在 peak_space=core_spectrum 时允许并校验边界；
- quant entity/run/sample 引用和 measurement enum 合法；
- metadata counts 与实际记录一致；
- source_fields/source_tables/source_artifacts 可按冻结 JSON 规则序列化；
- 不允许绝对路径、用户名、临时目录、当前时间或随机 ID 进入业务 payload。

物理 Validator 当前 MS2→Precursor 双向要求必须继续保持；不能为 BU 放宽。

## 23. P2-C2 生产实施文件清单

只有解决第 28 节 blocker 后，P2-C2 才可实施。预期新增：

- binary_layer/bottom_up_schema.py
- binary_layer/bottom_up_bundle.py
- binary_layer/bottom_up_adapter.py
- binary_layer/tools/real_bottom_up.py
- binary_layer/bottom_up_validator.py
- binary_layer/bottom_up_reader.py
- tests/fixtures/bottom_up/ 中最小合成 fixture
- tests/test_bottom_up_inspector.py
- tests/test_bottom_up_adapter.py
- tests/test_bottom_up_tool.py
- tests/test_bottom_up_validator.py
- tests/test_bottom_up_reader.py
- tests/test_bottom_up_service.py

预期最小修改：

- binary_layer/models.py：仅增加显式 bundle/profile/options 类型；
- binary_layer/inspector.py：识别 real_dia_result_bundle；
- binary_layer/plan.py：source_type→固定 named steps；
- binary_layer/registry.py 与 tools/__init__.py：只注册 named tool；
- binary_layer/service.py / __init__.py：仍只公开 convert_source_to_zp；
- 统一 validate_zp 与 Reader façade：组合业务层，不改变物理 dispatch；
- README.md：输入合同、Extension、Admission、Reader/Validator。

PipelineRunner 不得出现 DIA/DDA/MS level/鉴定分支；StepRegistry 不得选计划；RealBottomUpTool 只返回 BlockCollection；ZpWriter 仍是唯一写边界。

如果最终决定让 core_precursors.charge 可空、或让 DIA MS2 不需要 core precursor，则属于 core field/relationship 变化：必须评估新 ZP_VERSION，并同时更新 constants、models、serialization、writer、reader、validator、tests、README。v1/v2 不得被静默重解释，DEFAULT_ZP_WRITE_VERSION 仍保持 1。

## 24. 测试结果

本阶段只新增 Markdown 调查报告，没有生产代码、fixture、Registry 或格式改动。

本报告写入后的验收结果：

~~~text
报告结构：29/29 个规定编号章节
报告绝对源路径泄漏检查：0 命中
报告行尾空白：0 命中
python -m pytest（报告首次写入后）：924 passed in 54.20s
python -m pytest（测试结果回填后）：924 passed in 53.45s
existing failures：0
new failures：0
git diff --check：exit 0（仅既有 LF→CRLF warning）
未跟踪文本空白检查：92 个文本文件，0 命中
~~~

后续仅补充了点名实体字段的逐项证据表；按仓库要求仍使用同一 python -m pytest 命令完整复跑，最终结果在任务交付时复核。

## 25. 架构边界复核

| 边界 | 结果 |
|---|---|
| DEFAULT_ZP_WRITE_VERSION=1 | 未改 |
| v1/v2 物理布局 | 未改 |
| 九个顶层块及顺序 | 未改 |
| core_chromatograms/extensions 为空仍存在 | 未改 |
| 唯一 ZpWriter | 未改 |
| 一个 .zp 一个 run | 草案明确保持 |
| 统一 convert_source_to_zp | 草案明确保持 |
| Tool 仅生成 Block/BlockCollection | 草案明确保持 |
| PipelineRunner 无质谱业务分支 | 草案明确保持 |
| StepRegistry 仅 name→step | 草案明确保持 |
| Bottom-Up 结果只进 extensions | 草案明确保持 |
| 不使用 scan/charge 哨兵 | 明确禁止 Viewer 的 -1 泄漏 |
| 不复制 Top-Down Schema | 使用 DIA precursor identification/BU entities |

## 26. 临时文件和真实数据保护结果

- D:\dia-shuju 全程只读。
- 未修改、重命名、移动、删除或覆盖任何真实文件。
- 未在源目录创建派生文件、临时 .zp、缓存或日志。
- 没有解压/覆盖 zip。
- pickle 只做 opcode/结构检查，未执行不受信反序列化。
- SQLite 使用只读查询；mzML/XML/TSV/Parquet 只读。
- 没有把真实大型数据复制到仓库。
- 没有保留调查临时目录；本阶段未创建临时调查文件。
- 报告仅保存必要相对身份与 hash；展示的源机器路径已省略。

## 27. Git 状态

测试后复核确认，本阶段唯一新增路径：

~~~text
?? docs/P2_C1_BOTTOM_UP_INVESTIGATION.md
~~~

两个仓库的 cached diff 仍为空。E:\viewer 保持原有一个 modified 文件和 mzml-demo/tests/ 未跟踪目录；E:\viewer-two 的所有既有 tracked/untracked 工作保持原样。最终一次 status/diff check 在交付前再次执行。

## 28. 剩余风险

### 阻塞性风险

1. DIA MS2 core precursor 的合同阻塞已由 P2-C1.1 解除：每个 DIA MS2 可关联一个显式 `isolation_window` core precursor，charge/mz/intensity 为 null，绝对窗口边界为权威字段。
2. 完整谱图忠实性仍是 P2-C2 实现要求：不得只写 MS1、把 MS2 放进 BU JSON，或把 DIA MS2 伪装为 DDA selected precursor。

### 非阻塞但需 P2-C2 明确

1. source_tables 对 3.5M library rows的 v1 JSON 体积/内存成本，需要流式构建与大小门禁；不能因此丢数据。
2. pos/infoneg 是不安全 pickle；必须走可信 bridge 或明确 preserve-only artifact。
3. all/target 同一有效 ID 的量化值不同，Reader 必须让 stage/source role 可见。
4. DIA-NN report 无源生 scan/rank，派生关联必须标 provenance 和误差。
5. Bruker .d peak arrays 需要受控 decoder；B 目前只能作未来 DIA raw source。
6. FASTA 有一个 report accession 未命中；允许 list_only coverage。
7. 当前 Viewer single-run name fallback 太宽松；.zp Inspector 草案已收紧为精确匹配。

## 29. 最终判定

本调查原始判定中的唯一阻塞：

~~~text
reason=dia_ms2_core_precursor_contract_unresolved
~~~

已由后续独立阶段 P2-C1.1 解除。统一合同在同一 `core_precursors` 块中区分
`selected_precursor` 与 `isolation_window`；旧记录缺 kind 时兼容推断为前者，
DIA 窗口使用 null charge 和绝对上下界，不修改九块结构、v1/v2 物理布局或
默认 v1。完整决策与证据见 `docs/P2_C1_1_DIA_PRECURSOR_CONTRACT.md`。

因此 P2-C1 通过，可以进入 P2-C2 DIA-NN Bottom-Up 生产实现；这不表示
P2-C2 本身已经开始或通过。
