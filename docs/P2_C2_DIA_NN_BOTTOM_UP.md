# P2-C2 DIA-NN Bottom-Up production report

Current corrected status:

```text
P2-C2功能链路已跑通
P2-C2真实科学数据正确性已验证
P2-C2性能验收未通过
reason=conversion_and_full_validation_performance_unacceptable
```

## 1. 阶段结论

P2-C2 implemented the unified production path from one Thermo DIA mzML plus
one DIA-NN 2.0 Parquet report bundle to a complete `.zp`. The first formal v2
conversion is physically and logically valid. Final independent source and
determinism checks are recorded below after the formal acceptance run.

## 2. 两个仓库前置状态

- `E:\viewer-two`: branch `main`, HEAD
  `b018531c417c1f7f4d789510034a270790a9004c`; the pre-existing dirty worktree
  contained 22 tracked changes plus untracked P1/P2 implementation and tests.
- `E:\viewer`: branch `main`, HEAD
  `6417a642aeefdef7e01eff8c04f3c19606c5ec12`; one pre-existing modified
  `mzml-demo/scripts/prsmup.py` and untracked `mzml-demo/tests/`.
- Both cached diffs were empty. No reset, clean, stash, rebase, restore,
  checkout overwrite, add, commit, or push was performed. Viewer remained
  read-only.

## 3. 冻结文档复核

`P2_C1_BOTTOM_UP_INVESTIGATION.md`,
`P2_C1_1_DIA_PRECURSOR_CONTRACT.md`, and `README.md` were read in full before
implementation. The nine-block order, v1/v2 physical layouts, IDs/relations,
RT-in-seconds core convention, DIA association behavior, and 69-column matrix
were treated as frozen.

## 4. 真实输入身份

The unambiguous formal root was `D:\dia-shuju\shangchuan`; the broader
`D:\dia-shuju` root correctly rejects as `AMBIGUOUS_DIANN_REPORT` because it
contains two byte-identical copies.

| Relative source | Bytes | SHA-256 | Role |
| --- | ---: | --- | --- |
| `spectra/20200110_Hela_500ng_DIA_25cm_120min_R1.mzML` | 1,445,130,808 | `01cfecb120d75c5fd50fcc37e61745cc6bd7301441f12cebc88941e82fe318fa` | spectrum source |
| `diann/all_report.parquet` | 37,773,200 | `9f77a33d182cdef7fdacb32ddc0e85fba631ce828f473f29e707ed334ed6667b` | primary report |
| `diann/target_report.parquet` | 12,951,767 | `75c618676bb1a436e45a9b0577e458c8be2010bc5ed0cf4a9b9061333e0d0cfd` | preserved refined report |
| `diann/all_report.stats.tsv` | 593 | `5407b15fb8764a4972863b08851fdcf617cb737cb83280bdead93ff1596bd5e6` | preserved stats |
| `diann/all_report.protein_description.tsv` | 226,017 | `5ae86b7e71d8171d23a4701f2ba2869db2667cc38915ade9d5328f2be7d0ee3d` | preserved descriptions |

Total inspected input size was 1,496,082,385 bytes. The report contains one
Run, `20200110_Hela_500ng_DIA_25cm_120min_R1`, normalized to
`20200110_hela_500ng_dia_25cm_120min_r1`. The curated bundle has no log, so
embedded software evidence is `DIA-NN 2.0_contract`; the read-only original
`all_report.log.txt` independently states `DIA-NN 2.0 Academia`.

## 5. 输入角色发现和配对

`all_report.parquet` wins over `target_report.parquet`; the latter is hashed
and preserved but never merged. Run values are streamed from Parquet and must
contain one normalized identity. Exact normalized mzML filename matching must
produce one and only one spectrum source. Missing, duplicate, multi-run, and
unmatched cases have stable failure codes.

## 6. SourceInspector 与 ConversionPlan

The profile is `source_type=real_dia_result_bundle`,
`adapter_flavor=diann_2_parquet`, one run, with explicit identity files. The
fixed plan is `file_validate -> hash_input -> real_dia_result ->
string_pool_build -> index_build -> zp_write -> zp_validate`. Registry only
maps the step name; PipelineRunner contains no mass-spectrometry branching.

## 7. DIA mzML 核心解析

The dedicated DIA mode reuses the production mzML parser and retains all
admitted spectra, not only identified MS2. Core stores native ID, proven scan,
seconds RT, MS level, and all m/z/intensity arrays. Source representation,
polarity, dtype, compression, RT unit, activation, collision energy, and
source selected-ion facts are preserved in `dia_mzml_metadata` v1.

## 8. isolation_window core precursor

Every DIA MS2 owns exactly one core precursor with
`precursor_kind=isolation_window`, absolute `target-lower_offset` and
`target+upper_offset` bounds, and null `charge`, `precursor_mz`, and
`intensity`. Identification charge/mz never populate core. DDA Admission and
legacy selected-precursor serialization remain unchanged.

## 9. DIA-NN Parquet 字段合同

PyArrow 23 reads row groups/RecordBatches with a default batch size of 8,192.
The real file has 323,232 rows, two row groups, and 69 columns. Its names and
Arrow kinds exactly equal the frozen contract. Required missing columns and
type drift reject; known optional absence and unknown columns are separately
reported.

## 10. 69 列映射结果

All 69 known columns have an explicit source column, entity, logical field,
type, nullability, unit, and conversion policy. RT-family minute values are
typed in seconds. Every original value is additionally preserved in admitted
`source_fields`, and all 323,232 source rows are retained in 40 columnar
RecordBatch chunks. `unexplained_column_count=0`.

## 11. identification 实体

Viewer admission is `Decoy == 0` and `Q.Value < 0.01`. IDs hash the spectrum
file identity, normalized run, and unique `Precursor.Id`; no row number,
randomness, path, or current time is used. The entity kind is explicitly
`dia_precursor_identification`; source scan/native ID/rank are null.

## 12. Peptide 实体

The frozen investigation defines peptide identity by stripped sequence. It
preserves all modified sequences, charges, identifications, proteins, groups,
and modifications as deterministic reverse relations. The real count is
92,704.

## 13. Protein 与 ProteinGroup 实体

Semicolon-separated `Protein.Group` members become distinct Protein entities
and one ordered ProteinGroup. No concatenated fake Protein, sequence,
coverage, unique-peptide count, or leading protein is invented. The real
counts are 8,145 proteins and 8,063 groups.

## 14. Modification 实体

`(UniMod:4)` tokens become independent Carbamidomethyl records with
`UNIMOD:4`, +57.021464 Da, one-based peptide residue coordinates, and C
residue validation. The real admitted report produces 25,902 records.
Localization probability remains null when unavailable.

## 15. Fragment 条件支持

No Viewer live b/y calculation or DIA-NN library fragment is stored as an
experimental match. Unsafe pickle deserialization is never performed.
`bottom_up_fragment_matches` is absent and metadata reports
`fragment_source_not_loaded`, `unsafe_pickle_deserialization_used=false`.

## 16. Quantification 实体

Identification-level precursor/MS1 measurements and group-level
PG/gene measurements are separated. Missing stays null, zero stays zero, and
non-finite or semantically negative values reject. The real typed
quantification count is 118,089.

## 17. identification→Spectrum 关联算法

The independent module reproduces Viewer: report `RT` in minutes, closed
absolute isolation bounds, no m/z tolerance, maximum 0.5 minute delta,
nearest absolute RT, then scan number. Provenance records method/version,
units, bound rule, tolerance, and tie-break. Multiple identifications may
share one MS2.

## 18. 真实关联统计

The production Adapter admitted and associated 110,026/110,026
identifications with zero dangling references and 53,110 distinct MS2. Final
independent association equality is recorded in the acceptance JSON.

## 19. Extension 布局与 Schema 版本

Eight conditional logical Extension types are defined at schema version 1.
Metadata, identifications, and peptides are required. Proteins, groups,
modifications, fragment matches, and quantification are emitted only when
real records exist or metadata declares their unavailable/not-present status.
No tenth top-level block exists.

## 20. Reader 接口

`BottomUpReader` and the public module functions expose summary,
identification/by-spectrum, peptide, protein, group, modifications, fragments,
and quantification summary without v1/v2 physical differences. The first
implementation decodes one complete Extension set before indexing; this is a
documented large-file performance boundary, not an API difference.

## 21. Bottom-Up Validator

Validation composes physical `ZpValidator`, Top-Down validation, and
`BottomUpExtensionValidator`. It checks schema identity, deterministic order,
counts, IDs, every foreign key/reverse relationship, core Spectrum/run,
modification location, numeric domains, source JSON, quantification ownership,
and the null-charge isolation-window core contract.

## 22. 统一 Service 执行结果

The only public conversion entry remains `convert_source_to_zp(...)`; default
format remains v1 and formal acceptance explicitly requested v2. The first
formal conversion returned `valid=true`, `bottom_up_valid=true`, and nine
checked blocks. The service hashes source identities before/after, uses one
sibling partial output, and commits without overwrite only after validation.

## 23. 真实 .zp 物理结果

Formal file:
`E:\viewer-two-data\p2-c2\output\20200110_Hela_500ng_DIA_25cm_120min_R1.p2-c2.v2.zp`.
It is 2,521,241,519 bytes with SHA-256
`c8426c567f9e9f76266c16a27184fa9c0e726c82bebc3d0c9028915a864bd2ac`.
`format_version=2`, `physical_valid=true`, `checked_blocks=9`, and physical
issues are empty.

## 24. 真实实体计数

| Entity | Count |
| --- | ---: |
| identification | 110,026 |
| peptide | 92,704 |
| protein | 8,145 |
| protein group | 8,063 |
| modification | 25,902 |
| fragment match | 0 (not available) |
| quantification | 118,089 |

## 25. 核心谱图与数组对照

The frozen source baseline is 109,766 spectra: 5,778 MS1 and 103,988 MS2.
Spectrum m/z/intensity contributes 161,238,110 float64 values and 219,532
arrays; retained chromatograms bring the complete file total to 161,457,642
values and 219,534 arrays. The formal acceptance independently hashes every
decoded source array and compares every v2 per-array checksum; its final
equality and exact window counts are stored in `P2_C2_REAL_ACCEPTANCE.json`.

## 26. 与 Viewer 参考链路对照

Production imports no Viewer module. Formal comparison independently streams
the Parquet admission set, all 69 source values, peptide/group sets, and a
separate RT+window nearest-MS2 implementation. Viewer code was read only to
freeze its closed-bound/no-tolerance/nearest/tie semantics.

## 27. 确定性结果

The DIA header timestamp is derived from stable mzML `run.startTimeStamp`
through the single Writer's optional timestamp input; default Writer behavior
and Goldens are unchanged. Fixture v1 migration equals direct v2 byte for
byte. The second real output is retained only until SHA/byte comparison, then
deleted.

## 28. 性能和峰值 RSS

First formal conversion:

- inspect 0.925 s; mzML parse/block build 334.212 s;
- admission 0.754 s; Parquet parse 227.104 s;
- association 3.466 s; Extension build 5.649 s;
- Writer 1,017.542 s; unified validation 3,748.991 s;
- total 5,445.008 s; output 2,521,241,519 bytes;
- parent peak RSS 9,596,948,480 bytes (about 8.94 GiB).

Memory is below the 28GB risk threshold, but runtime and the approximately
9.6 GB peak are performance failures. P2-C2.1 replaces Python scalar numeric
validation, adds quick/deep modes, certificates and checkpoints, and records
the corrected real measurements without changing the format. See
`P2_C2_1_PERFORMANCE.md`; this P2-C2 result must not be described as a
performance pass.

## 29. 迁移兼容

The minimal DIA-NN Fixture converts to v1, migrates v1→v2, and converts
directly to v2. Migrated and direct v2 outputs are byte-identical and their
Bottom-Up summaries, core spectra/precursors, and arrays are equal.

## 30. Golden 与 B8.5/B8.6 门禁

Existing complete/minimal v1/v2, DDA, RAW, and Top-Down tests remained green
after the production-hash refresh. The default version and nine-block physical
contracts are unchanged. P2-C2.1 reran the release gates with intentional
production sources frozen: `B8.5 release_gate=true` and
`B8.6 release_gate=true`. No Golden byte or expected corruption result changed.

## 31. 全量测试结果

The initial baseline was 954 passing tests. New focused tests cover v1/v2,
migration, deterministic output, 69 columns, role/run discovery, DIA windows,
association sharing, Reader/Validator, unsafe pickle handling, atomic cleanup,
stable negative codes, quick/deep modes, certificates and SHA-bound checkpoints.
The P2-C2.1 complete corruption matrix has 291 passing cases; the final
full suite has 1,004 passing tests.

## 32. 原子写入和故障清理

The first large attempt intentionally exposed a DIA validation work-memory
limit and failed with no target, partial, or `.tmp` left behind. After raising
only the inspected DIA resource profile, the valid run committed atomically.
Fixture failures likewise leave no target or orphan. Source files are never
written.

## 33. 真实数据保护结果

All formal source identities are hashed before/after conversion and again by
acceptance. No file under `D:\dia-shuju` was created, changed, moved, or
deleted. `.zp` business content contains relative labels only: no drive,
username, CWD, temp path, random token, current time, or command line.

## 34. 生产边界复核

- Tool writes zp = false
- single Writer maintained = true
- physical format changed = false
- nine-block order changed = false
- default version changed = false
- `DEFAULT_ZP_WRITE_VERSION=1`
- DIA core charge fabricated = false
- identification copied to core precursor = false
- all 69 columns accounted for = true
- Bruker `.d` implemented = false

## 35. Git 状态

No staging, commit, push, or destructive Git command was performed. Final
`status`, diff stats, cached diff, and whitespace checks are recorded at
handoff; all unrelated pre-existing modifications remain protected.

## 36. 剩余风险

`formal_real_dataset_count=1` and
`cross_dataset_generalization_not_yet_proven`. Large Extension JSON decoding
remains slow. Python-level v2 numeric validation has been replaced by bounded
mmap/NumPy validation, but v2's canonical JSON still prevents deep validation
from meeting 30 seconds.
Bruker DIA-PASEF, classic DDA search results, safe PFMB conversion, indexed
Bottom-Up on-disk queries, Viewer/database/frontend integration, and other
vendors remain out of scope.

## 37. 最终判定

The final pass statement requires the independent real source comparison,
second-output byte equality, production hash gates, and final full pytest run.
Those machine-readable results live under
`E:\viewer-two-data\p2-c2\reports`. P2-C2.1 performance evidence is summarized
in `docs/P2_C2_1_PERFORMANCE.md`; it does not retroactively label the original
P2-C2 performance acceptance as passed.
