# P1-B8 production implementation plan

Status: **P1-B8.1 completed on 2026-07-14; P1-B8.2 has not started.**

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

**Failure rollback.** Remove v2 Reader wiring/module; retain v2 Writer fixtures
for offline validation; v1 Reader remains unchanged.

## P1-B8.4: v2 Validator

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

**Failure rollback.** Remove v2 Validator dispatch/module while keeping v2
Reader/Writer explicitly experimental; do not weaken v1.

## P1-B8.5: v1/v2 compatibility and full Golden fixtures

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
