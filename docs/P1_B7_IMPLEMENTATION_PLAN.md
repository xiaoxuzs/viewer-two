# P1-B8 production implementation plan

Status: **P1-B8.5R completed on 2026-07-14; P1-B8.5 requires a full rerun; P1-B8.6 has not started.**

Date: 2026-07-14 (Asia/Shanghai)

This plan implements the frozen P1-B7 design in independent acceptance gates.
No stage may reinterpret v1, leak physical encoding into BlockTools, add
mass-spectrometry branching to Runner/Registry, or change the default Writer
version without a later explicit decision. At the start and end of every
stage, run the complete suite and audit protected v1 fixtures/contracts.

## P1-B8.1: versioned Reader/Writer/Validator skeleton

**Status: completed on 2026-07-14.**

Completion record:

- `ZP_VERSION=1` remains the compatibility/default production export; version 1 and known version 2 now have distinct constants, while read/write/validate implemented-version sets contain only version 1.
- `ZpWriter.write(..., *, format_version=1)` defaults to the frozen v1 path. Explicit version 2 fails before path creation, Block validation, serialization, temporary-file creation, or replacement.
- `ZpReader` validates the fixed 24-byte Header magic and endianness before dispatch. Version 1 retains the existing path; version 2 and unknown versions stop before directory or block parsing with distinct errors.
- `ZpValidator` retains the complete v1 checks and issue ordering. A valid version-2 top-level Header returns `ZP_V2_VALIDATION_NOT_IMPLEMENTED` with zero checked blocks; unknown versions retain `UNSUPPORTED_VERSION`.
- Known-but-unimplemented errors carry `version`, `operation`, and stable operation-specific codes. Unknown Writer/Reader requests carry `UNSUPPORTED_ZP_VERSION` semantics.
- The original 304-test baseline and completed 327-test suite pass. Mock and real mzML Pipelines still require no version argument and produce version 1; v1 arrays directory encoding remains `json`.
- P1-B8.2 may begin only with these dispatch/fail-closed tests green, v1 production hashes protected outside the allowed facade files, and no default-version or Pipeline-layer version selection change.

**Goal.** Add Header-first facade dispatch and isolated v1/v2 interfaces with
v2 methods failing closed until their stage exists. Preserve current public
v1 behavior and default v1 output.

**Allowed files.** `binary_layer/reader.py`, `writer.py`, `validator.py`,
version-specific new I/O modules, exceptions/constants needed for explicit v2
identities, focused facade/dispatch tests, README/docs.

**Forbidden files/changes.** Core Block dataclasses; Runner, Registry,
RealMzmlParseTool, adapter/admission; v1 layout/fixtures; a functional binary
arrays encoder/decoder; default v2 output.

**Tests.** v1 regressions; Header versions 1/2/unknown; exact version/encoding
mismatch matrix; extension does not dispatch; v2 operations fail with stable
not-implemented/unsupported codes rather than falling into v1.

**Acceptance.** All existing v1 bytes and errors remain; facades dispatch only
after magic/Header validation; omitted Writer version still writes v1.

**Failure rollback.** Revert facade modules and new dispatch tests together;
the original v1 classes remain the runnable path.

## P1-B8.2: v2 arrays Writer

**Status: completed on 2026-07-14.**

Completion record:

- `binary_layer/v2_arrays_writer.py` independently implements the frozen
  64-byte Header, canonical directory, UTF-8 ID ordering, zero padding, and
  contiguous raw little-endian float64 payload. Production code does not
  import the reference Codec.
- Layout preparation validates all fields and values and incrementally computes
  per-array SHA-256 in fixed 8,192-value chunks. The second pass writes directly
  to the target temporary stream while incrementally computing the whole arrays
  checksum; it creates neither a complete payload byte string nor a complete
  arrays-block byte string.
- Writer defaults remain v1. Explicit v2 derives `global_meta.format_version=2`
  without mutating the input Blocks, writes eight `utf-8-json` blocks and one
  `zp-arrays-v2` block, then flushes, `fsync`s, and atomically replaces using
  the existing sibling `.tmp` lifecycle.
- Immutable Writer limits default to 512 MiB arrays block, 64 MiB directory,
  100,000 entries, 16,000,000 values per array, 4,096 UTF-8 bytes per ID, and
  448 MiB payload. Predictable failures occur before directory or temporary-file
  creation; injected mid-write failures remove the temporary file and preserve
  an existing target.
- Production arrays bytes equal both committed P1-B7 Golden fixtures exactly.
  Independent full-file parsing verifies the 24-byte Header, all nine ordered
  blocks and checksums, canonical EOF directory, encodings, and derived
  GlobalMeta version. The reference Codec separately validates extracted arrays.
- The suite grew from 327 to 368 passing tests. Default/explicit v1 bytes,
  mock and real Pipeline v1 behavior, and v2 Reader/Validator fail-closed
  behavior remain covered.
- Direct v2 writes succeeded for MS1-only (2 spectra, 4 arrays, 7,276 bytes),
  MS1/MS2 (2 spectra, 1 precursor, 4 arrays, 7,565 bytes), and TIC/BPC
  (1 spectrum, 2 chromatograms, 6 arrays, 7,840 bytes) BlockCollections.
- On the small MS1/MS2 fixture, five-run median Writer times were 1.782 ms for
  v1 and 1.771 ms for v2; sizes were 6,865 and 7,565 bytes. The isolated v2
  `tracemalloc` run peaked at 78,375 bytes (6.014 ms), with observed process RSS
  peak 91,361,280 bytes. The previously measured 31 MB sample was not present
  in this workspace, so no new large-sample result is claimed.
- B8.3 may begin only while the v1 default and production hashes outside the
  allowed Writer files remain frozen, Golden regeneration is deterministic,
  and Reader work preserves target-only access and both checksum levels.

**Goal.** Encode the frozen 64-byte Header, canonical directory, zero padding,
contiguous raw-le float64 payload, per-array checksums, and top-level arrays
checksum under explicit `format_version=2`.

**Allowed files.** v2 Writer module, shared checked-arithmetic/safety helpers,
Writer facade wiring, v2 Writer tests and full-file v2 Golden fixture builder.

**Forbidden files/changes.** Block fields or BlockTool behavior; v1 Writer;
Parser/Runner/Registry; compression/float32; streaming architecture that lets
a BlockTool write payload bytes; default-version switch.

**Tests.** independent literal struct/offset assertions; Unicode byte-order
sorting; zero/empty arrays; checksums; determinism; atomic temp/fsync/replace;
resource limit rejection before excessive packing/allocation; v1 byte
regression.

**Acceptance.** Explicit v2 writes exactly the P1-B7/Golden bytes, retains all
nine blocks, never repairs missing logical data, and cleans temp files on every
failure.

**Failure rollback.** Remove v2 Writer wiring/module/fixtures; explicit v2
selection returns the B8.1 fail-closed result; v1 stays default.

## P1-B8.3: v2 arrays Reader and random access

Status: **completed on 2026-07-14.**

**Goal.** Parse v2 top-level and internal directories and implement true
target-only `read_array`, Spectrum arrays, and Chromatogram arrays.

**Allowed files.** v2 Reader module, checked range/strict JSON helpers, facade
wiring, file-source instrumentation tests, Reader benchmarks.

**Forbidden files/changes.** v1 Reader semantics; global cache; whole payload
read in `read_array`; complete `BlockCollection` construction per target read;
checksum skipping; Viewer integration.

**Tests.** all Header/directory/range/schema corruptions; exact seek/read byte
instrumentation; target checksum only; another array may be corrupt without
breaking target read; full decode does detect it; Unicode IDs; cache lifetime
and file identity changes.

**Acceptance.** A target read performs directory reads plus exactly one target
payload read, verifies that checksum/numeric semantics, and matches full
logical decoding.

**Completion record.** `ZpReader` now dispatches v1 unchanged and v2 through
the independent `binary_layer/v2_arrays_reader.py`; production Reader code
imports neither the Writer implementation nor the reference Codec. It strictly
parses canonical top-level and internal JSON with duplicate-key rejection,
checks the encoding matrix/ranges/EOF/Header/padding/contiguous offsets, and
applies the frozen seven read limits before large reads or full decoding.
Per-instance caches contain only the top and arrays directories and are keyed
by `(resolved_path, st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`;
same-path replacement and mid-read identity changes invalidate or fail, while
payload bytes and decoded values are never cached.

Single and batched target reads sort requested entries by payload offset, read
only those bytes, verify only their per-array checksums, and preserve requested
return order. Full `read_arrays` first applies its 1 GiB default conservative
decode budget, verifies the top arrays checksum, then verifies every per-array
checksum. Forty-five required top/arrays real-byte corruption classes plus
resource-limit and mid-read-change cases pass. A deterministic 128-Spectrum,
512-peaks/Spectrum baseline produced 1,372,870-byte v1 and 1,157,954-byte v2
files; cached v2 Spectrum payload I/O was exactly 8,192 target bytes and zero
unrelated payload bytes. On this one development run, v1/v2 single-Spectrum
times were 46.25/2.22 ms and random100 times were 3.955/0.229 s; these are
environment baselines, not production performance claims. A final rerun after
I/O instrumentation recorded 43.37/1.32 ms and 3.675/0.150 s respectively;
the variability is why time is informational while exact payload bytes are the
gate. The 31,408,514-byte real source was restored for the final gate: its v2
file was 42,559,842 bytes with a 39,067,064-byte arrays block and 4,098 entries.
Explicit v2 writing took 10.434 s; first/cached `read_array` took 76.18/2.57 ms,
single Spectrum 16.61 ms, sequential10 0.112 s, random100 1.357 s, repeat100
1.231 s, Chromatogram 3.21 ms, and full arrays 2.905 s. The selected Spectrum
read exactly 37,264 target payload bytes, zero unrelated payload bytes, 709,934
total bytes, and four seeks. These remain one-environment baselines.

**B8.4 entry condition.** The default Writer/Pipelines remain v1, Validator is
unchanged and returns `ZP_V2_VALIDATION_NOT_IMPLEMENTED`, Golden fixtures are
stable, and the full Reader/corruption/I/O suite is green. B8.4 may implement
only the v2 Validator and must reuse or reconcile the already proven read-only
parsing rules without weakening v1.

**Failure rollback.** Remove v2 Reader wiring/module; retain v2 Writer fixtures
for offline validation; v1 Reader remains unchanged.

## P1-B8.4: v2 Validator

Status: **completed on 2026-07-14.**

**Goal.** Implement complete top-level, arrays Header/directory/padding,
per-array, numeric, and cross-block relationship validation.

**Allowed files.** v2 Validator module, facade wiring, shared read-only parsing
helpers proven in B8.3, corruption/resource/reference tests.

**Forbidden files/changes.** v1 Validator relaxation; data synthesis/repair;
using random-read validation as full validation; accepting mismatched
version/encoding or unknown fields.

**Tests.** every P1-B7 reference corruption plus full-file block checksum,
range/order/EOF; Spectrum/Chromatogram missing/wrong-type/length relations;
empty format versus domain rules; all limits before allocation; v1 error-code
regressions.

**Acceptance.** Complete validation verifies top-level arrays SHA-256, every
per-array checksum/value, and all references; error codes are deterministic;
no invalid Fixture is accepted to make a test pass.

**Completion record.**

- `ZpValidator` remains the public facade. Its v1 implementation and issue
  order are unchanged; Header version 2 dispatches to the independent
  `binary_layer/v2_validator.py` implementation. The v2 path imports neither
  production Reader/Writer code nor the reference Codec.
- Validation order is Header, strict canonical top directory, eight complete
  JSON blocks and their checksums/schemas, binary arrays Header/directory and
  padding, one sequential payload scan, then cross-block references,
  statistics, indexes, and mzML extension schemas. Unsafe structural errors
  stop before attacker-provided offsets are followed.
- One payload chunk simultaneously updates whole-arrays SHA-256, current-array
  SHA-256, and little-endian float64 semantic checks. Only `array_id`, type,
  count, byte length, and checksum state survive the scan; payload bytes and
  decoded values are not retained. Instrumented tests prove payload bytes read
  equal payload length, scan count is one, and each read is at most the
  configured chunk.
- Frozen safe defaults are 512 MiB arrays, 64 MiB top/internal directories,
  100,000 entries, 16M values per array, 4096 UTF-8 bytes per ID, 448 MiB
  payload, 64 MiB validation working memory, and a 256 KiB chunk. Invalid
  configurations fail explicitly and resource issues report location,
  actual, and limit before large reads or allocations.
- Spectrum, Precursor, Chromatogram, Run, GlobalMeta, StringPool, Index, and
  known mzML Extension relationships are checked against directory metadata;
  finite negative intensity remains valid while NaN/Infinity and negative
  m/z/time are rejected.
- The suite grew from 443 to 543 passing tests. New gates cover more than 79
  distinct corruption/resource/reference categories, deterministic issue
  priority, three checksum-isolation layers, file replacement, single-stream
  use, and unchanged v1/Writer/Reader/Pipeline behavior.
- Small TIC/BPC validation measured 7,840 bytes, six arrays, 12 values,
  0.00773 s, 68,062 traced bytes, and one payload scan. The deterministic
  128x512 medium gate measured 1,157,691 bytes, 256 arrays, 131,072 values,
  5.596 s, 1,430,167 traced bytes, and one payload scan. These are
  one-environment baselines, not production claims.
- The restored 31,408,514-byte source produced a temporary 42,559,978-byte v2
  file with a 39,067,064-byte arrays block, 4,098 arrays, and 4,762,968 values.
  Full validation returned `valid=True`, zero issues, and nine checked blocks;
  all 38,103,744 payload bytes were read exactly once, the largest read was
  28,992 bytes under a 256 KiB chunk, and Reader summary round-trip passed.
  The monitored validation took 246.510 s, traced peak was 47,894,000 bytes,
  sampled RSS peak was 384,163,840 bytes, and the large output was removed.

**B8.5 entry condition.** Keep the default Writer/Pipelines and `ZP_VERSION`
at version 1, retain complete v1/v2 validation, regenerate Golden arrays
deterministically, and keep all protected production hashes outside the B8.4
allowlist unchanged. B8.5 has not started.

**Failure rollback.** Remove v2 Validator dispatch/module while keeping v2
Reader/Writer explicitly experimental; do not weaken v1.

## P1-B8.5R: Validator Extension owner semantic correction

**Status: completed on 2026-07-14. P1-B8.5 remains incomplete.**

Completion record:

- The first B8.5 audit found one concrete semantic drift: a
  `mzml_auxiliary_arrays` record pointing to a missing Chromatogram was valid
  under the v1 Validator but produced `INVALID_REFERENCE` under v2.
- The v1 Validator now parses that already-decoded Extension payload through
  the frozen `MzmlAuxiliaryArraysV1` schema and validates owner references
  against one Spectrum-ID set and one Chromatogram-ID set. It reports schema
  errors before reference errors and preserves Extension/array input order.
- The frozen `OwnerKind` enum contains Spectrum and Chromatogram, while the
  only currently admitted auxiliary array (`MS:1000786` `ms level`) permits
  Chromatogram owners only. No new owner kind or auxiliary-array capability
  was added.
- The added work is `O(core IDs + Extension entries)`, with no Reader call,
  file reopen, JSON reparse, checksum pass, or arrays-payload scan. A 10,000
  owner test proves exactly one set-membership lookup per auxiliary record.
- Both committed 3,086/3,502-byte failure Fixtures now return
  `INVALID_REFERENCE` with nine checked blocks. Their SHA-256 values remain
  `289553f0...a82f5` and `2629f288...a27de`, and deterministic regeneration
  remains green.
- The suite contains 563 passing tests. Only `binary_layer/validator.py`
  changed in production; v2 Validator, Writer, Reader, Schema, wire format,
  default v1 output, Pipeline, Registry, Runner, and Tools remain unchanged.
- P1-B8.5 must now be rerun in full. P1-B8.6 has not started and may not start
  from this correction gate alone.

## P1-B8.5: v1/v2 compatibility and full Golden fixtures

**Status: previous audit did not pass; full rerun required after B8.5R.**

**Goal.** Freeze complete-file v1 and v2 Goldens and prove one public facade
reads/validates both without semantic drift.

**Allowed files.** small full-file Golden fixtures/manifests/builders, facade
compatibility tests, docs and fixture inspection CLI.

**Forbidden files/changes.** deleting/rebuilding historical v1 fixtures under
new semantics; production feature expansion; default Writer switch; large
binary fixtures.

**Tests.** independent Header/directory/block checksums; version/encoding
matrix; logical equality across paired Goldens; deterministic regeneration;
old v1 corruptions retain codes; unknown versions reject.

**Acceptance.** Both versions are safely distinguishable and readable, v1
bytes remain frozen, and committed fixtures are small, independently audited,
and byte-deterministic.

**Failure rollback.** Remove only new full-file v2 fixtures/tests; retain
arrays-subformat P1-B7 fixtures and previous accepted production stages.

## P1-B8.6: v1-to-v2 migration tool

**Goal.** Add `zp-migrate-v1-to-v2` with read-only source, full before/after
validation, exact logical comparison, atomic distinct target, and structured
report.

**Allowed files.** a migration CLI/module outside BlockTools, command entry,
migration report model, atomicity/equality/failure tests and docs.

**Forbidden files/changes.** in-place source replacement; mzML reparse;
BlockTool/Runner/Registry integration; value/ID normalization; silent target
overwrite; default Writer change.

**Tests.** same-path/symlink rejection; invalid source; all logical blocks and
values; injected write/fsync/validate/replace failures; temp cleanup; existing
target policy; exact report fields; source hash/bytes unchanged after every
case.

**Acceptance.** `source.zp -> source.v2.zp` is atomic, target fully valid and
logically equal, source untouched, and every failure is recoverable with a
stage/error report.

**Failure rollback.** Remove CLI/entry/report/tests; dual-version Reader and
Writer continue without migration claims.

## P1-B8.7: real mzML v1/v2 comparison

**Goal.** Convert the accepted real mzML corpus explicitly to both versions
and compare logical outputs, disk size, memory phases, validation, and target
reads.

**Allowed files.** benchmark-only runners/results/docs, explicit version
selection at the final Writer boundary, test-only comparison utilities.

**Forbidden files/changes.** RealMzmlParseTool version awareness; adapter,
admission, Blocks, Runner/Registry business branching; acceptance expansion;
format changes motivated by one sample.

**Tests.** small deterministic corpus plus 31.4 MB sample; arrays exact values;
all IDs/references/extensions; v1/v2 validation; repeat runs; cleanup and hard
resource guards.

**Acceptance.** v1 and v2 are logically equal; v2 demonstrates target-only
access and materially improved arrays size/memory evidence without changing
the source-domain result.

**Failure rollback.** Retain production stages but mark v2 trial blocked;
remove generated large files and keep compact failure evidence.

## P1-B8.8: performance and release gates

**Goal.** Set evidence-backed v2 admission limits, concurrency/temp budgets,
Reader-cache invalidation acceptance, checksum cost budgets, packaging, and
trial release criteria.

**Allowed files.** benchmark/monitoring tools, compact result summaries,
configurable v2 safety defaults, operational docs and release tests.

**Forbidden files/changes.** automatic default-v2 switch; deleting v1 paths or
tests; unreviewed compression; Viewer dependency; hiding top-level or
per-array checksum work to improve numbers.

**Tests.** multiple sizes/repeats/cold reads; hostile length/count corpus;
aggregate concurrency; cache mtime/size/handle replacement; checksum timing;
disk/temp failure; cross-language fixture probes where available.

**Acceptance.** limits are measured and enforced before allocation, release
rollback is documented, v1 compatibility remains green, and v2 is eligible
only for explicit trial. Changing the default Writer requires a new gate.

**Failure rollback.** Restore prior conservative v2 limits or disable explicit
v2 writes; retain safe dual readers for existing trial artifacts and keep v1
as default.
