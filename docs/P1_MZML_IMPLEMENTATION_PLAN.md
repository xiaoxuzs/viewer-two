# P1-B real mzML implementation plan

Status: **future implementation plan only; P1-A does not implement real mzML conversion.**

Date: 2026-07-13 (Asia/Shanghai)

This plan implements only the strict support profile in `P1_MZML_INVESTIGATION.md`. The frozen `.zp` v1 core Blocks, format layout, Writer, Reader, and generic Runner model remain unchanged. Each stage is a separate review gate.

## Global gates

- Freeze samples, acceptance decisions, and versioned extension schemas before writing `RealMzmlParseTool`.
- Never infer missing scan, charge, selected-ion intensity, RT unit, precursor, or array semantics.
- Never select the first of multiple precursors or selected ions.
- Never leak parser dictionaries, NumPy objects, or XML elements into Blocks/extensions.
- Runner stays generic; PlanBuilder selects the parser step.
- Commit `context.blocks` only after successful EOF and cross-reference checks.
- Stop for a v2 review if any v1 core field would need reinterpretation.

## P1-B1: fixtures, dependency, and extension schemas

### Goal

Freeze executable accept/reject decisions, a minimal fixture corpus, the tested Pyteomics range, and parser-neutral schemas for `mzml_metadata` v1 and `mzml_auxiliary_arrays` v1.

### Modify files

- `pyproject.toml` after version testing.
- New `binary_layer/mzml_schema.py`.
- New `tests/fixtures/mzml/` and `tests/test_mzml_schema.py`.
- Investigation doc only if new evidence changes a stated assumption.

### Do not modify files

- `binary_layer/tools/`, Inspector, PlanBuilder, Registry, Runner.
- Core Blocks, constants, Writer, Reader, Validator.

### Tests

- Indexed/non-indexed; seconds/minutes; float32/64; zlib/uncompressed fixture checks.
- Missing scan/charge/intensity, multiple precursor/ion, unknown array and chromatogram decision fixtures.
- Extension round-trip, determinism, owner refs, limits, malformed/unknown version rejection.
- Pyteomics import/version tests on supported Python/Windows.

### Acceptance

Fixtures cover every initial support boundary; schemas are versioned, bounded, documented and library-neutral; the dependency range is evidence-backed. No production parser exists.

### Failure rollback

Remove this isolated dependency/schema/fixture change. Runtime remains the P0 mock-only baseline.

## P1-B2: SourceInspector and plan migration

### Goal

Classify `.mzML`/`.mzml` as `real_mzml`; make tests construct `mock_mzml` profiles explicitly; wire the future real step fail-closed.

### Modify files

- `binary_layer/inspector.py`, `plan.py`, `registry.py`.
- Inspector/PlanBuilder/Registry tests and mock-profile helper.
- README status wording.

### Do not modify files

- Runner, core Blocks, Writer, Reader, Validator, constants.
- No magic test extension or Inspector test/runtime mode.

### Tests

- Case-insensitive real classification and unrelated-extension rejection.
- Explicit mock profile keeps the P0 plan.
- Real plan order is validate, hash, real parse, string pool, index, write, validate.
- Existing generic Runner tests pass unchanged.

### Acceptance

Production mzML cannot reach the mock parser; mock behavior requires explicit test construction; Runner has no source-specific branch.

### Failure rollback

Revert Inspector/plan/registry/test-helper wiring together; never leave `.mzML` routed to mock parsing.

## P1-B3: Run and MS1 parser

### Goal

Implement a Pyteomics adapter and atomic candidate construction for the single-run centroid MS1 subset, with proven scan extraction, RT normalization, arrays, IDs, counts, and metadata extensions.

### Modify files

- New `binary_layer/tools/real_mzml.py`.
- `binary_layer/mzml_schema.py` only for fixture-proven corrections.
- Registry only to replace a temporary fail-closed stub.
- New `tests/test_real_mzml_ms1.py` and atomicity tests.

### Do not modify files

- Core Block definitions/meanings, Writer, Reader, Runner, Validator, format version.

### Tests

- Indexed/non-indexed, seconds/minutes, float32/64, zlib/uncompressed pass cases.
- Ambiguous scan, bad RT unit, profile, missing/mismatched arrays, non-finite/negative m/z, unsupported encoding, undeclared auxiliary arrays reject.
- Failure leaves blocks/artifacts unchanged; extension payload has no parser-library objects.

### Acceptance

Supported MS1 creates all required v1 Blocks without reinterpretation; every unsupported construct fails semantically before commit; iteration releases source records.

### Failure rollback

Remove the concrete parser and restore B2 fail-closed wiring; never fall back to mock parsing.

## P1-B4: MS2 and precursor mapping

### Goal

Support exactly one precursor and selected ion with explicit m/z, nonzero charge and intensity; preserve parent reference, isolation, activation and collision energy in extensions.

### Modify files

- `binary_layer/tools/real_mzml.py` precursor adapter/ref resolution.
- `mzml_schema.py` only for reviewed precursor records.
- New `tests/test_real_mzml_ms2.py`.

### Do not modify files

- `PrecursorBlock`, `SpectrumBlock`, Writer, Reader, Runner, format version.
- No nullable/sentinel values or first-item selection.

### Tests

- One complete precursor passes.
- Missing/multiple precursor or ion, missing scalar, zero charge, ownership/ref failure, MS3+ and DIA reject atomically.
- Child Spectrum/Precursor ownership is bidirectionally consistent.

### Acceptance

Every required core scalar is explicit in source; unsupported precursor information is neither guessed nor dropped; a small MS1/MS2 fixture round-trips through existing Writer/Reader.

### Failure rollback

Revert MS2 changes and retain an explicit `ms_level != 1` rejection in the B3 parser.

## P1-B5: chromatogram strategy

### Goal

Support only TIC/BPC with explicit aligned time/intensity arrays and recognized time units. Preserve only schema-declared auxiliary arrays; reject every other nonempty chromatogram semantic.

### Modify files

- `binary_layer/tools/real_mzml.py` chromatogram adapter.
- Schema only for B1-justified records.
- New `tests/test_real_mzml_chromatograms.py`.

### Do not modify files

- Chromatogram/Array Blocks, core type enums, Writer, Reader, Runner.
- Never silently skip a nonempty chromatogram list.

### Tests

- TIC/BPC seconds/minutes pass; absent list maps to zero.
- Missing/mismatched arrays, non-finite values, unknown units, SRM/MRM/SIC, precursor/product semantics and undeclared auxiliary arrays reject.
- The observed TIC int64 `ms level` auxiliary array validates against its schema.

### Acceptance

Supported chromatograms retain values/provenance; unsupported nonempty chromatograms fail explicitly; declared/actual counts match.

### Failure rollback

Restore the pre-B5 rule that any nonempty chromatogram list rejects.

## P1-B6: Validator increments

### Goal

Validate strict-profile cross-block invariants and both extension schemas without turning Validator into an mzML parser.

### Modify files

- `binary_layer/validator.py`.
- Focused validator and corrupt-payload tests.
- Shared schema helper only when needed for deterministic validation.

### Do not modify files

- Writer, Reader, Runner, core Blocks, layout/version.
- No XML/CV/compression logic in Validator.

### Tests

- Counts, RT extrema, scan/native ID uniqueness.
- MS1/MS2 precursor ownership and scalar validity.
- Chromatogram references/lengths.
- Extension type/version/owner/schema and normalized-RT marker.
- All P0 checksum/corruption tests remain green.

### Acceptance

Each invariant has passing/failing tests; malformed or unknown extensions reject deterministically; Validator imports no source-parser dependency.

### Failure rollback

Revert this stage and keep real support unreleased until validation coverage returns.

## P1-B7: real round-trip and scale gate

### Goal

Exercise the entire path on tiny fixtures, the observed 31.4 MB file, and a substantially larger/high-peak sample; measure correctness, memory, time, output/temp size and cleanup.

### Modify files

- New `tests/test_real_mzml_roundtrip.py`.
- Opt-in `tests/integration/` manifest for non-redistributable samples.
- New `scripts/benchmark_mzml_conversion.py` and performance evidence doc.

### Do not modify files

- Runtime merely to make a benchmark pass without a separately reviewed defect.
- v1 semantics or acceptance thresholds after seeing results.

### Tests

- Inspector -> Plan -> Runner -> Writer -> Validator -> Reader.
- Exact IDs/counts/RT/arrays/precursors/chromatograms/extensions.
- No committed output or stale temp after failure.
- Record process peak RSS, Python traced peak, stage times, source/output/temp bytes and one-Spectrum read time.

### Acceptance

Correctness passes on fixtures and the real sample. A reviewed resource ceiling is met by the larger sample; otherwise P1-B stops for a storage/v2 decision. A 31 MB pass is not scalability proof.

### Failure rollback

Retain evidence but do not declare P1-B support; revert unreviewed tuning and open the format/storage decision.

## P1-B8: documentation and release acceptance

### Goal

Publish the exact support/rejection profile, dependency matrix, errors, extension schemas, performance evidence and v2 deferrals while preserving P0 guarantees.

### Modify files

- README, P1 docs, release checklist and test manifests.

### Do not modify files

- Runtime implementation during this documentation gate.
- Frozen P0 semantics.

### Tests

- Full suite/package checks and documentation-link checks.
- Protected-file diff/hash audit.
- Repeat the P1-A fifteen-point adversarial review.

### Acceptance

Docs state exactly what is supported, rejected, preserved and deferred; all tests pass without unexpected skips; evidence names parser versions, fixtures, resource limits and gaps; no claim exceeds testing.

### Failure rollback

Keep README status experimental/not implemented and do not publish P1-B acceptance.

## Planned production file set

Expected new files:

- `binary_layer/mzml_schema.py`
- `binary_layer/tools/real_mzml.py`
- `tests/test_mzml_schema.py`
- `tests/test_real_mzml_ms1.py`
- `tests/test_real_mzml_ms2.py`
- `tests/test_real_mzml_chromatograms.py`
- `tests/test_real_mzml_roundtrip.py`
- `scripts/benchmark_mzml_conversion.py`

Expected existing-file changes: `pyproject.toml`, Inspector, PlanBuilder, Registry, Validator, test helpers, README and P1 docs.

Expected untouched for the strict v1 subset: `constants.py`, `models.py`, `blocks.py`, `writer.py`, `reader.py`, and `runner.py`. If a stage needs one of these, stop and re-review before editing.
