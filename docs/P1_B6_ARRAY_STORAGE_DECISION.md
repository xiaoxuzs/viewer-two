# P1-B6 array storage decision

Status: **decision complete; no new storage format is implemented here.**

Date: 2026-07-14 (Asia/Shanghai)

## 1. Decision background

The frozen v1 `arrays` block is one canonical JSON list. On the real 2,379,436-peak sample it occupies 74,610,555 bytes (95.5281% of `.zp`), and the full output occupies 32.8243 bytes per peak. `read_spectrum_arrays` parses this whole list and rebuilds an ID map every time; one real Spectrum costs about 1.46-1.52 s and repeat100 costs 154.5 s.

P1-B6 compares alternatives but must not change the Header, directory, core Blocks, Writer, Reader, Validator or version.

## 2. Invariants

- one `.zp` is the unified movable intermediate artifact;
- all nine top-level logical blocks remain conceptually present;
- `ArrayBlock` IDs remain unique and references remain stable;
- BlockTools still produce typed logical data, never write bytes;
- only `ZpWriter` writes the final file;
- corruption is detected by explicit checksums;
- float64 remains the lossless normalized baseline until a scientific precision review says otherwise;
- old v1 files must remain safely readable or explicitly rejected by version-aware code;
- seconds remain the RT/time convention.

## 3. Encoding microbenchmark

Five representative real arrays were used: short/long m/z, an intensity array, chromatogram time, and chromatogram intensity (10,053 total values). Sizes exclude any future directory so they compare identical numeric payloads.

| Encoding | Bytes | vs JSON | Encode ms | Decode ms | Single array ms | Full scan ms | SHA-256 ms | Exact? | Float32 error |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| canonical JSON float64 | 143,895 | 1.000 | 8.544 | 1.864 | 1.754 | 1.928 | 0.082 | yes | none |
| binary little-endian float64 | 80,424 | 0.559 | 0.141 | 0.285 | 0.062 | 0.298 | 0.069 | yes | none |
| binary little-endian float32 | 40,212 | 0.279 | 0.125 | 0.146 | 0.034 | 0.146 | 0.027 | no | 2,048 values; max abs `1.21891e-4`, max rel `5.83168e-8` |
| per-array zlib float64 | 45,795 | 0.318 | 5.360 | 0.792 | 0.118 | 0.465 | 0.043 | yes | none |
| per-array zlib float32 | 35,556 | 0.247 | 1.482 | 0.346 | 0.083 | 0.351 | 0.027 | no | same measured float32 loss |

Raw float64 is lossless and much faster to encode/decode than JSON for these arrays. zlib float64 is smaller but costs compression time. The result does not authorize float32: some values changed, and no scientific/Viewer tolerance study was performed.

## 4. Candidate 0: retain one v1 JSON arrays list

Advantages: frozen compatibility, human inspection, no implementation change, no new dependency, cross-language JSON. Disadvantages: 95.5% file dominance, Python-float expansion, full-block serialization/validation, no physical random access, and repeated parse cost. Recommended maximum is the bounded v1 gate: 64 MiB input, 5M peaks, 200 MiB output, 4 GiB predicted RSS, with earlier warnings.

This remains the v1 compatibility format, not the future primary production format.

## 5. Candidate 1: one arrays region, internal directory, contiguous binary payloads

Concept:

```text
arrays region
├─array directory: id, type, dtype, offset, byte length, value count, encoding, checksum
└─contiguous little-endian payloads
```

The nine top-level logical blocks can remain, but `arrays` changes physical encoding from one JSON object to a structured binary region. A Reader can read the internal directory once, then seek exactly to one payload. Each payload can have an exact checksum and later an explicit compression code. Core BlockTools may continue producing logical arrays; the Writer alone converts them to physical bytes.

Writer changes: version-aware array directory construction, checked offsets/lengths, float64 packing, optional later compression, bounded-write design. Reader changes: version dispatch, directory validation, single-payload read/decode. Validator changes: directory/reference/range/non-overlap checks, per-array checksum, dtype/count validation, semantic relationships without full numeric decode where possible.

This is the **preferred** design target.

## 6. Candidate 2: per-array chunks inside a block-internal subdirectory

Each array is an explicit chunk with its own header/checksum/encoding. It gives excellent random access, corruption isolation and parallel construction. Costs are more small-chunk metadata, alignment/padding decisions, more complex atomic directory construction, and a larger attack surface for overlaps/duplicates. It is the **alternative** if P1-B7 proves that independent chunk lifecycle/parallel writing matters more than a compact contiguous payload region.

## 7. Candidate 3: Spectrum-grouped chunks

Grouping m/z and intensity by Spectrum makes the common single-Spectrum read fast. It handles chromatograms awkwardly, weakens the general independent `array_id` contract, complicates arrays shared or queried outside a Spectrum, and makes payload organization depend on an MS business owner. It risks leaking business grouping into a generic array store and is not recommended.

## 8. Candidate 4: external sidecar

`run.zp` plus `run.zp.arrays` can provide simple offsets and independent replacement, but violates the default one-file artifact goal. Moving, copying, upload, hashing, cache invalidation, cleanup, atomic install, and partial-loss recovery become two-file lifecycle problems. A sidecar is not the default recommendation.

## 9. Candidate 5: SQLite/HDF5/Arrow/Parquet container

These provide mature indexing or columnar storage, but change `.zp` from a controlled purpose-built format into a wrapper around a third-party format/runtime. SQLite gives random blobs but limited typed-array conventions; HDF5 adds native/runtime deployment complexity; Arrow/Parquet favor columnar scans and interoperability but do not directly solve the exact per-array/checksum/single-file semantics without design work. They add dependencies, cross-version compatibility obligations, Viewer packaging work, and less byte-layout control. No dependency is introduced in P1-B6.

## 10. Score matrix

Scores are 1 (poor) to 5 (strong). Columns: disk, Writer memory/time, Validator time, Spectrum/Chromatogram read, checksum, damage isolation, implementation simplicity, v1 compatibility, cross-language, compression, memory mapping, parallel write, BlockTool boundary, single-file.

| Candidate | Disk | WM | WT | VT | Spec | Chrom | Csum | Damage | Impl | v1 | XLang | Comp | mmap | Par | Block | One file | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 v1 JSON | 1 | 1 | 2 | 1 | 1 | 1 | 2 | 1 | 5 | 5 | 5 | 1 | 1 | 1 | 5 | 5 | 38 |
| 1 directory + contiguous binary | 5 | 4 | 5 | 4 | 5 | 5 | 4 | 4 | 4 | 1 | 5 | 5 | 5 | 4 | 5 | 5 | 73 |
| 2 per-array chunks | 4 | 4 | 4 | 5 | 5 | 5 | 5 | 5 | 3 | 1 | 5 | 5 | 4 | 5 | 5 | 5 | 70 |
| 3 Spectrum chunks | 4 | 4 | 4 | 4 | 5 | 2 | 4 | 4 | 3 | 1 | 4 | 5 | 4 | 5 | 2 | 5 | 58 |
| 4 sidecar | 4 | 4 | 4 | 4 | 5 | 5 | 4 | 4 | 3 | 1 | 5 | 5 | 5 | 4 | 4 | 1 | 62 |
| 5 third-party container | 5 | 4 | 4 | 4 | 5 | 5 | 5 | 5 | 2 | 1 | 5 | 5 | 4 | 4 | 2 | 4 | 65 |

Candidate 1 wins because it combines compact lossless float64, direct array offsets, one-file deployment, format control, and a clean logical/physical boundary with less metadata/implementation complexity than per-array chunks.

## 11. ZP_VERSION decision

**Changing arrays from JSON to binary requires `ZP_VERSION=2`.**

Reasons:

1. stored bytes and array-region layout change;
2. the top-level directory `encoding` meaning changes;
3. v1 Reader supports only JSON and cannot safely interpret an internal binary directory;
4. Validator range/checksum/schema behavior changes;
5. old v1 files must retain frozen interpretation;
6. a new Writer would produce bytes old Readers reject;
7. Header version exists precisely to distinguish incompatible physical contracts.

Keeping nine logical block names is not proof of byte compatibility. Reusing version 1 would silently reinterpret the frozen format and is rejected.

## 12. Compatibility strategies

### A. One Reader with explicit v1/v2 dispatch — selected

The fixed Header is read first; version 1 follows the frozen JSON path, version 2 follows the new binary arrays path. Shared high-level return types may remain logical Blocks, but byte parsers and validators are version-specific. This centralizes public API use while keeping unsafe reinterpretation impossible.

### B. Separate `V1Reader` and `V2Reader`

This gives maximum implementation isolation but duplicates directory/header/public APIs and forces every caller/Viewer integration to choose a class. It is a useful internal structure, not the preferred public surface.

### C. Offline migration first

Migration can reduce long-term v1 files, but cannot be a prerequisite for opening existing prototypes and does not remove the need to recognize both Header versions safely. Migration is optional follow-up tooling.

The value of old P0 mock files is regression/contract evidence, not a reason to write new v1 production-scale data indefinitely.

## 13. Writer impact

P1-B7 must design a v2 Writer that packs validated float64 payloads, constructs offsets without business repair, calculates explicit checksums, writes atomically, and has a bounded-memory strategy. P1-B6 does not implement any of those changes. `ZpWriter` remains the sole final-file writer.

## 14. Reader impact

The v2 path should read only Header/top-level directory/internal array directory for lookup, then seek to the requested payload. It must state cache lifetime and memory cost; no hidden global cache. v1 stays full-block JSON. The API must not claim disk random access unless the v2 path actually performs it.

## 15. Validator impact

v2 validation must check top-level and internal directory bounds, overlap, unique IDs, dtype/encoding/value counts, checksums, and logical references. It must not synthesize missing arrays or indexes. Validation should avoid mandatory full numeric decode where checksum and metadata checks suffice, while retaining optional deep numeric validation for finite/nonnegative domain rules.

## 16. BlockTool impact

No business branching belongs in `PipelineRunner` or `StepRegistry`. BlockTools continue to create typed logical Blocks. Physical packing is a Writer concern. If bounded memory later requires a new handoff contract, it must be an explicit reviewed architecture change rather than a benchmark flag or a BlockTool writing bytes.

## 17. Rejected shortcuts

- Do not make float32 the default: precision changed in the measured set and scientific tolerance is unresolved.
- Do not merely zlib-compress one giant JSON block: it reduces disk but preserves full-block decode, poor access, and memory overlap.
- Do not add a Reader cache to hide v1 cost in P1-B6.
- Do not use a sidecar as default without accepting two-file atomicity/lifecycle semantics.
- Do not adopt a third-party container solely because it benchmarks well on a different workload.

## 18. Final decision

```text
Preferred: ZP v2 single arrays region with an internal array directory and contiguous little-endian float64 payloads.
Alternative: ZP v2 per-array chunks with a block-internal subdirectory.
ZP_VERSION decision: version 2 is mandatory for the new physical encoding.
Compatibility: one version-dispatching Reader with frozen v1 and explicit v2 paths.
```

Compression should be an explicit per-array or reviewed chunk encoding in v2, not silently enabled. Raw float64 is the baseline; zlib float64 is a measured optional encoding candidate.

## 19. P1-B7 scope

Only one next stage is recommended: **P1-B7: ZP v2 binary array format design and compatibility plan.** It must freeze byte layout, internal directory/checksum rules, float64 encoding, resource-bounded Writer behavior, v1/v2 Reader/Validator dispatch, migration test strategy, and versioned fixtures before implementation.

P1-B7 must not be marked complete by this decision record.

## 20. Explicit non-goals of P1-B6

No binary arrays, per-array chunks, compression in production, memory mapping, streaming/chunked Writer, new Reader, Viewer/RAW/database integration, or format migration was implemented.

