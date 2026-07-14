# P1-B6 real mzML scale and memory assessment

Status: **measurement and decision record complete; final acceptance still requires the closing regression/hash audit.**

Date: 2026-07-14 (Asia/Shanghai)

## 1. Investigation goal

P1-B6 measures the frozen version-1 JSON implementation. It does not optimize it. The questions are how disk size, Python/native memory, phase time, and Reader cost grow; whether a single JSON `arrays` block remains a reasonable production storage unit; and what format decision should enter P1-B7.

The production Header, nine-block directory, Blocks, Writer, Reader, Validator, `PipelineRunner`, `StepRegistry`, `RealMzmlParseTool`, and `ZP_VERSION=1` were not changed.

## 2. Current v1 structure and first-principles cost

Each Spectrum owns two logical `ArrayBlock` records. Each record repeats `array_id`, `array_type`, `dtype`, `values`, JSON punctuation, and decimal representations. The real sample has 2,379,436 peaks, hence 4,758,872 Spectrum m/z/intensity values plus 4,096 chromatogram values. A peak therefore stores two JSON numbers, not one.

For the real sample:

- whole `.zp`: 78,103,277 bytes, or **32.8243 bytes per peak**;
- `arrays`: 74,610,555 bytes, **95.5281%** of the file;
- arrays bytes per numeric value: **15.6647**;
- m/z decimal tokens: 42,953,696 bytes;
- intensity decimal tokens: 31,261,858 bytes;
- time decimal tokens: 30,266 bytes;
- all numeric decimal tokens: 74,245,820 bytes;
- array record structure/metadata: 360,636 bytes;
- header: 24 bytes; directory tail including its length: 1,410 bytes.

Thus roughly 31.33 bytes per peak are the two decimal numeric streams alone; other blocks and directory overhead raise the total to 32.82 bytes per peak. The input mzML is smaller because its float64 arrays are packed as eight-byte binary values and zlib-compressed before base64. Conversion decodes that compressed binary, widens it into Python objects, and emits uncompressed decimal UTF-8 JSON. zlib source compression is not preserved by the v1 physical arrays encoding.

## 3. Test environment

| Item | Value |
|---|---|
| Python | 3.12.7 |
| Pyteomics | 4.7.5 |
| OS | Windows 11 `10.0.26200` |
| CPU | Intel64 Family 6 Model 140, 8 logical CPUs |
| Physical memory | 16,885,276,672 bytes (15.73 GiB) |
| Workspace volume | 209,715,195,904 bytes total; media type could not be confirmed without elevated WMI access |
| RSS backend | Windows `GetProcessMemoryInfo`; optional psutil was not installed |
| Python memory | `tracemalloc` |

The unconfirmed disk media type is reported as unavailable, not guessed.

## 4. Measurement method

- Every scale point runs in a fresh Python child process.
- The parent enforces `--max-rss-gb`, `--max-runtime-seconds`, and `--max-output-gb`; a breach produces `RESOURCE_LIMIT_REACHED` and removes partial output.
- The worker builds the actual `SourceProfile`, actual `PlanBuilder` plan, and actual default Registry, then executes the named production Steps in order.
- Benchmark-only timing wrappers observe the existing parser, admission, candidate builder, canonical serializer, and Validator methods. They do not replace results or change business branching.
- RSS is sampled every 50 ms; Windows `PeakWorkingSetSize` is also retained when available.
- The cold real run records seven tracemalloc snapshots and the top 20 allocation sites at each stage. Snapshot wall time (97.35 s) is separately reported and removed from adjusted pipeline/Writer timing. The two repeat runs omit snapshots but retain tracemalloc and RSS.
- Each Reader cold operation runs in a new process. Batch operations intentionally use the unmodified Reader repeatedly.
- Validator checksum attribution replays SHA-256 per stored block after a full production validation. It is labeled replay, not silently substituted for validation.

## 5. Metric limitations

`tracemalloc` sees Python allocator traffic but not complete NumPy, decompression-library, OS cache, and other native allocations. RSS includes those native allocations, the interpreter, allocator arenas, and loaded libraries, but does not assign them to individual objects. A 50 ms sampler can miss a brief current-RSS spike; Windows lifetime peak working set reduces that risk.

The timing wrappers and tracemalloc add overhead. In particular, an untraced standalone Validator attribution pass took 3.318 s while traced conversion Validator phases took 10.93-17.71 s. The reported conversion series is internally consistent and conservative, not a claim about uninstrumented throughput.

The synthetic mzML data is deterministic and schema-compatible, but its decimal patterns and metadata differ from a vendor acquisition. Linear fits describe the observed range; they do not prove the 5M-100M extrapolated range.

## 6. Real 31.4 MB sample: three complete runs

Input: `E:\viewer-TD\test\xzx_PXD045330\20191118_rvg262_LT_110516-13_1000-1100_Techrep01.mzML` (never modified or copied).

Common counts: 2,048 Spectra, 997 MS1, 1,051 MS2/precursors, 1 TIC, 4,098 core arrays, 2,379,436 peaks. Every output was 78,103,277 bytes and valid.

| Run | Role | Parse s | Writer s | Validator s | Reader summary s | Pipeline s | tracemalloc peak | RSS peak | arrays bytes | bytes/peak |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cold | cold + snapshots | 14.485 | 19.024 | 10.930 | 17.384 | 62.712 | 473,501,014 | 1,720,324,096 | 74,610,555 | 32.8243 |
| repeat1 | repeated | 24.461 | 24.307 | 15.977 | 16.707 | 82.567 | 471,928,798 | 1,646,055,424 | 74,610,555 | 32.8243 |
| repeat2 | repeated | 21.744 | 25.343 | 17.705 | 16.579 | 82.554 | 471,695,290 | 1,586,601,984 | 74,610,555 | 32.8243 |

Output ratio is **2.486691x** input in every run. Output-size standard deviation is zero; nondeterministic Header timestamps changed bytes/checksums but not length.

### Three-run statistics

| Metric | Minimum | Median | Maximum | Population stddev |
|---|---:|---:|---:|---:|
| parse seconds | 14.485 | 21.744 | 24.461 | 4.211 |
| Writer seconds | 19.024 | 24.307 | 25.343 | 2.767 |
| Validator seconds | 10.930 | 15.977 | 17.705 | 2.874 |
| pipeline seconds | 62.712 | 82.554 | 82.567 | 9.357 |
| Reader summary seconds | 16.579 | 16.707 | 17.384 | 0.353 |
| tracemalloc peak bytes | 471,695,290 | 471,928,798 | 473,501,014 | 801,875 |
| RSS peak bytes | 1,586,601,984 | 1,646,055,424 | 1,720,324,096 | 54,703,393 |

The repeated runs were not faster. Results are preserved individually rather than averaged away.

## 7. Synthetic scale results

The generator emits real base64, real zlib, valid indexed offsets, about 50/50 MS1/MS2, exactly one supported precursor per MS2, and one TIC. Pyteomics opens the generated files and the production pipeline accepts them.

| Scale | Spectra | Peaks | Input bytes | `.zp` bytes | Pipeline s | RSS peak | tracemalloc peak | bytes/peak | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| S1 | 128 | 32,768 | 473,498 | 796,433 | 1.853 | 116,568,064 | 6,355,683 | 24.3052 | valid |
| S2 | 512 | 262,144 | 2,538,029 | 5,570,876 | 11.544 | 271,032,320 | 46,664,073 | 21.2512 | valid |
| S3 | 2,048 | 2,097,152 | 15,349,027 | 41,781,122 | 79.687 | 1,510,100,992 | 361,877,187 | 19.9228 | valid |

S1 float32/uncompressed/non-indexed also passed: 607,871-byte input, 1,038,478-byte output, 32,768 peaks, 1.871 s pipeline, and 115,314,688-byte RSS peak. Its output was larger than the zlib/float64 S1 output because v1 stores normalized decimal float values; source dtype/compression does not determine output compactness.

S4 was optional and was not run. S3 and the real point already reached 2.10M and 2.38M peaks, and the resource-safety rule takes precedence over chasing the largest point.

## 8. Phase timing

The cold real run separated:

| Phase | Seconds | Meaning |
|---|---:|---|
| file validation | 0.0057 | existence/type/read byte/stat |
| SHA-256 input hash | 0.0348 | complete 31.4 MB input |
| Pyteomics + XML parse | 14.4850 | production `parse_mzml` |
| admission | 0.0336 | frozen feature-policy evaluation |
| candidate Block build | 0.3801 | `_build_candidate`, no derived blocks |
| StringPool | 0.0011 | production Step |
| Index | 0.0107 | production Step |
| canonical block serialization | 18.8719 | all nine JSON payloads |
| Writer file/checksum/fsync/replace remainder | 0.1523 | Writer total minus serialization and snapshot pause |
| production Validator (traced) | 10.9304 | full validation |
| Reader summary | 17.3835 | multiple full block reads used by conversion summary |

An untraced Validator-only pass split 3.3180 s into 1.6442 s JSON parsing, 1.1836 s schema checks, 0.3229 s relationship checks, and 0.0623 s replayed per-block SHA-256. Remaining time is directory/block I/O and Python overhead. Checksum was not skipped.

## 9. Memory attribution

| Snapshot | Current traced bytes | Peak traced bytes | Interpretation |
|---|---:|---:|---|
| start | 0 | 0 | tracing boundary |
| after_parse | 158,924,680 | 161,882,086 | immutable parsed tuples/Python floats; adapter float conversion is dominant |
| after_candidate_blocks | 201,287,148 | 201,306,562 | parsed document plus candidate Block lists coexist |
| after_writer_serialization | 241,554,417 | 313,615,736 | parsed/block arrays plus nine canonical JSON payloads |
| after_write | 164,554,220 | 313,615,736 | serialized payload dict released; Block arrays remain |
| after_validator | 165,657,767 | 473,501,014 | Validator loaded/decoded all blocks, creating the overall Python peak |
| after_reader | 166,752,591 | 473,501,014 | Reader repeats complete JSON parsing; peak already set by Validator |

At `after_parse`, `mzml_adapter.py:392` accounts for about 152.6 MB and 4.77M traced allocations: tuple-contained Python floats for two values per peak. Candidate lists add about 42 MB current traced memory. Canonical JSON adds a 74.6 MB arrays payload plus other payloads. The Writer retains `BlockCollection` and serialized bytes simultaneously and writes a 78.1 MB sibling temporary file; the temp file is disk occupancy, not another in-RAM byte buffer.

Validator subsequently reads, hashes, and JSON-decodes the entire arrays block into a separate parsed object graph while `context.blocks` still exists, driving the traced peak to 473.5 MB. RSS reaches 1.59-1.72 GB, about 5.0-5.5x input bytes and 3.35-3.63x traced peak, demonstrating the native/allocator gap. Concurrent imports multiply this pressure.

## 10. File block decomposition

| Block | Bytes | File fraction | Records | checksum s | JSON decode s |
|---|---:|---:|---:|---:|---:|
| global_meta | 487 | 0.0006% | 1 | 0.000017 | 0.000014 |
| string_pool | 97,371 | 0.1247% | 1 | 0.000075 | 0.000242 |
| core_runs | 345 | 0.0004% | 1 | 0.000003 | 0.000025 |
| core_spectra | 672,646 | 0.8612% | 2,048 | 0.000507 | 0.004400 |
| core_precursors | 155,394 | 0.1990% | 1,051 | 0.000170 | 0.001816 |
| core_chromatograms | 249 | 0.0003% | 1 | 0.000007 | 0.000072 |
| arrays | 74,610,555 | **95.5281%** | 4,098 | 0.059281 | 1.469601 |
| indexes | 322,888 | 0.4134% | object | 0.000255 | 0.051310 |
| extensions | 2,241,908 | 2.8704% | 2 | 0.001633 | 0.018056 |

Arrays have 4,762,968 numeric values; lengths range from 4 to 3,624, median 717. Average array record size is 18,206.6 bytes. Key-name costs are small compared with decimal numbers (`array_id` keys 45,078 bytes, `array_type` 53,274, `dtype` 32,784, `values` 36,882), but they still scale with array count. `array_id` values alone cost 96,313 bytes.

## 11. Reader cost

Real `.zp`, each cold item in a new process:

| Operation | Seconds | Peak RSS bytes | Actual behavior |
|---|---:|---:|---|
| Header | 0.000161 | 89,530,368 | 24 bytes |
| Directory | 0.000305 | 89,292,800 | header + tail JSON |
| core_spectra | 0.008595 | 91,111,424 | full spectra JSON |
| arrays | 2.062504 | 93,761,536 | full 74.6 MB arrays JSON |
| Spectrum first/middle/last | 0.0164-0.0189 | about 92 MB | full spectra JSON + linear search |
| Spectrum arrays first/middle/last | 1.459-1.519 | about 285.5 MB | full spectra + full arrays + ID map |
| Chromatogram block | 0.000346 | 89,174,016 | full tiny block |
| one Chromatogram + arrays | 1.424978 | 434,176,000 | full chromatogram + full arrays + ID map |
| sequential 10 Spectrum arrays | 14.9763 | 285,696,000 | ten full parses |
| fixed-seed random 100 | 160.9398 | 435,650,560 | 100 full parses |
| same Spectrum 100 times | 154.5007 | 435,630,080 | 100 full parses; no cache |

S1 middle-Spectrum arrays took 0.0178 s versus 1.4592 s for the real output. Current `read_spectrum_arrays` first reads and parses all spectra, then reads/parses the complete arrays list and rebuilds an `array_id` dict. There is no cache and no disk-level single-array random access.

## 12. Scale models

Four points (S1/S2/S3 plus the median real point) were fit with `y = slope * peak_count + intercept`:

| Model | Slope | Intercept | R² | n |
|---|---:|---:|---:|---:|
| `.zp` bytes | 28.0203 B/peak | -1,861,768 B | 0.8951 | 4 |
| peak RSS bytes | 660.516 B/peak | 98,026,519 B | 0.9993 | 4 |
| Writer seconds | 1.14806e-5 s/peak | 0.4969 s | 0.9536 | 4 |
| Validator seconds | 7.23940e-6 s/peak | 0.5009 s | 0.9703 | 4 |

Array-count fits (same four points) yield RSS slope 395,738 bytes/array, R² 0.9893, and Writer slope 0.007007 s/array, R² 0.9797. They do **not** isolate array-object overhead: peak count, arrays, Spectra, metadata and decimal patterns co-vary. The negative fitted intercepts in some models are another warning not to use them near zero.

The two-point single-spectrum fit is `1.8645e-8 * zp_bytes + 0.00296 s`; R²=1 is mathematically inevitable for two points and is not validation.

## 13. Extrapolation (estimates, not tests)

| Peaks | Estimated `.zp` | Estimated RSS | Writer | Validator | One Spectrum arrays |
|---:|---:|---:|---:|---:|---:|
| 5,000,000 | 138.24 MB | 3.40 GB | 57.90 s | 36.70 s | 2.58 s |
| 10,000,000 | 278.34 MB | 6.70 GB | 115.30 s | 72.89 s | 5.19 s |
| 50,000,000 | 1.40 GB | 33.12 GB | 574.53 s | 362.47 s | 26.09 s |
| 100,000,000 | 2.80 GB | 66.15 GB | 1,148.56 s | 724.44 s | 52.21 s |

Decimal shape materially changes bytes/peak (19.9 synthetic versus 32.8 real), so these estimates carry wide uncertainty. They cannot prove OOM thresholds, filesystem behavior, corruption isolation, or parallel throughput.

## 14. v1 JSON resource thresholds

All hard maxima must pass; they are prototype admission gates, not production guarantees:

| Gate | Warning | Recommended hard maximum / reject |
|---|---:|---:|
| input size | 32 MiB | 64 MiB |
| peak count | 2,000,000 | 5,000,000 |
| predicted `.zp` | 80 MiB | 200 MiB |
| predicted RSS | 1.5 GiB | 4 GiB |

Reject if any hard maximum is exceeded or if free resources cannot reserve predicted RSS plus one output-sized temporary file. Apply the budget to aggregate imports: two allowed jobs together should remain below 50% of physical RAM. Warn at any warning threshold and recommend v2. The 5M peak maximum is supported only by extrapolation from a 2.38M measured ceiling and roughly 2x headroom; it is deliberately conservative and must be revisited with v2 measurements.

## 15. Current v1 applicability

v1 JSON arrays remain useful for fixtures, interoperability prototypes, and bounded single-file conversions below the gates. They are not a production-scale array store. A 31 MB input succeeding does not prove general production availability: output is 2.49x input, RSS reaches 1.72 GB, Validator repeats full-block loading, and one logical Spectrum access takes about 1.5 s because it parses the entire arrays block.

## 16. Risks

1. A modestly larger or less-compressible file crosses multi-GB RSS during candidate/serialization/validation overlap.
2. Viewer-like repeated single-Spectrum access scales with the whole run and creates severe latency/allocator churn.
3. Concurrent imports multiply RSS and temporary disk, so a gate based only on each input file is unsafe.

`tracemalloc` undercounts native memory; thresholds must use RSS predictions, not traced peak alone.

## 17. Raw result files

The retained aggregate is `benchmarks/results/p1_b6_summary.json`. It contains all three real rows, S1-S3 and the variant, block/array statistics, tracemalloc top-20 snapshots, Reader operations, encoding measurements, fits/R², extrapolations, thresholds, and production SHA-256 before/after. Per-run JSON files are working evidence and are removed during final cleanup after aggregation.

## 18. Adversarial audit (40 items)

1. Writer changed for better numbers? **No; production hash unchanged.**
2. Reader changed? **No.**
3. Hidden Reader cache? **No; repeat100 proves repeated parsing.**
4. Validator bypassed? **No; every conversion ran `zp_validate`.**
5. checksum skipped? **No; production validation plus replay attribution.**
6. only tiny fixtures? **No; real 31 MB and S1-S3.**
7. real sample once? **No; three complete runs.**
8. tracemalloc treated as all memory? **No; RSS is primary ceiling evidence.**
9. NumPy/native limit omitted? **No; explicitly documented.**
10. generated files retained? **No at final cleanup.**
11. large mzML committed? **No; generated path ignored.**
12. large `.zp` committed? **No.**
13. generator in production package? **No; `benchmarks/` only.**
14. invalid fake mzML? **No; Pyteomics and production parser read it.**
15. generator shares production parser logic? **No; independent XML/base64/zlib writer.**
16. estimates presented as measured? **No; extrapolation labeled.**
17. averages hide peaks? **No; min/median/max/stddev and every run retained.**
18. failed points ignored? **No failures; S4 is explicitly not run with reason.**
19. continued after OOM? **No OOM; parent guard would stop later points.**
20. v1 changed? **No.**
21. v2 implemented early? **No.**
22. `ZP_VERSION` changed? **No.**
23. only JSON vs one binary? **No; five encodings and six storage families.**
24. float32 loss omitted? **No; 2,048 changed values and errors measured.**
25. single-array access omitted? **No.**
26. checksum cost omitted? **No.**
27. sidecar recommended without lifecycle analysis? **No; analyzed and rejected as default.**
28. third-party container recommended without dependency analysis? **No; boundary-only comparison.**
29. no unique recommendation? **No; one preferred and one alternative are recorded.**
30. no v1 threshold? **No; explicit warning/hard gates above.**
31. 31 MB success called production-ready? **No.**
32. concurrent imports ignored? **No; aggregate 50%-RAM rule.**
33. Writer/Validator consecutive peaks ignored? **No; snapshot curve documents both.**
34. arrays fraction omitted? **No; 95.5281%.**
35. Reader repeated parse omitted? **No; quantified at 100 calls.**
36. Chromatogram read omitted? **No; 1.425 s with arrays.**
37. production tests changed for benchmark? **No existing expectations changed.**
38. nondeterministic scale data? **No; byte-equality test.**
39. unavailable metrics hidden? **No; disk media type and limitations are explicit.**
40. P1-B6 described as optimization complete? **No; it is evaluation/decision only.**

## 19. Three-month risk view

The most likely near-term failures are (1) an import above the current measured range exhausting memory during Validator, (2) UI/API callers assuming `spectrum_id` lookup is random I/O and creating minute-scale repeated reads, and (3) two individually admitted conversions overlapping and exhausting RAM/temp disk. Resource admission should remain active until v2 exists.

## 20. Next step

Only one next stage is proposed: **P1-B7: ZP v2 binary array format design and compatibility plan.** It designs and reviews the format; it does not enter this P1-B6 change.

