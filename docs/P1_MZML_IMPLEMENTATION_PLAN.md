# P1-B real mzML implementation plan

Status: **P1-B1 through P1-B5 completed; P1-B6 remains pending.**

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

Status: **completed on 2026-07-13. P1-B2 is complete; P1-B3 and later stages remain pending.**

Completion record:

- Pyteomics: installed/tested `4.7.5`; project constraint `pyteomics>=4.7.5,<5`.
- Fixtures: three accepted (`indexed float64 zlib MS1/MS2`, `non-indexed float32 uncompressed MS1/MS2`, `TIC+BPC with whitelisted ms-level auxiliary array`) and eight focused rejected fixtures listed in `tests/fixtures/mzml/manifest.json`.
- Frozen schemas: `binary_layer/mzml_schema.py` implements `mzml_metadata` v1 and `mzml_auxiliary_arrays` v1.
- Pure policy: `binary_layer/mzml_admission.py`. It is separate because extension serialization and conversion admissibility are independent contracts; neither module imports Pyteomics, NumPy, or lxml.
- Verification: 139 tests passed, including the original 42 P0 tests; the adjacent 31.4 MB real sample was accepted by the read-only test-side profile extractor with zero issues/warnings.
- Still unverified with vendor samples: negative mode, profile, Numpress, MS3+, DIA, ion mobility, multi-run, multi-precursor/selected-ion, SRM/MRM/SIC, missing required fields, and substantially larger/high-peak data.
- P1-B2 may start only while frozen P0 hashes remain unchanged, all 139 tests pass, fixture regeneration is byte-deterministic, and B2 remains limited to Inspector/PlanBuilder/Registry migration without implementing the parser.

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

Status: **completed on 2026-07-13. P1-B3 has not started.**

Completion record:

- `SourceInspector` now maps every case variant of `.mzML` to `real_mzml`; `.raw` remains `mock_raw`, and unknown extensions remain `unknown`.
- `PlanBuilder` emits the fixed real plan `file_validate -> hash_input -> real_mzml_parse -> string_pool_build -> index_build -> zp_write -> zp_validate` without querying the Registry.
- `mock_mzml` remains available only through an explicitly constructed `SourceProfile`; `mock_raw` retains its existing plan.
- `StepRegistry` required no source change. The default Registry contains only implemented steps and intentionally does not register `real_mzml_parse`.
- A real mzML entry therefore completes file validation and hashing, then fails through `StepNotFoundError` wrapped by `StepExecutionError`; no mock step, Block, output path, or `.zp` is produced.
- Inspector, Plan, Registry, Runner fail-closed, and explicit mock regression coverage brings the suite to 146 passing tests.
- `examples/build_mock_zp.py` now constructs its mock `SourceProfile` explicitly while retaining PlanBuilder, Runner, Writer, Reader, and Validator execution.
- P1-B3 may begin only with Fixture-driven development using the existing schemas and admission policy. It must not modify Runner or allow the real plan to fall back to mock before `RealMzmlParseTool` exists.

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

Status: **completed on 2026-07-13. P1-B4 is also complete.**

Completion record:

- `binary_layer/mzml_adapter.py` is the only production Pyteomics/NumPy dependency boundary. It performs one Pyteomics Spectrum iteration with immediate conversion to immutable Python facts; a lightweight XML structure pass supplies run/source/entity metadata, Chromatogram features, and auxiliary-array CV facts without decoding Spectrum arrays a second time.
- `RealMzmlParseTool` is a `BaseBlockTool` named `real_mzml_parse`. It evaluates the existing admission policy, adds only the B3 MS2/Chromatogram capability gates, builds a complete local candidate, validates counts/references, and assigns `context.blocks` once.
- The default Registry registers one concrete `RealMzmlParseTool` instance without inspecting source type or plans. Runner, core Blocks, Writer, Reader, Validator, format version, and frozen admission/schema semantics are unchanged.
- Added deterministic `accept_ms1_only_indexed_float64_zlib.mzML` and `accept_ms1_only_nonindexed_float32_uncompressed.mzML` fixtures. Both contain two centroid MS1 Spectra with proven Thermo scan numbers and no Chromatograms.
- Supported boundary: one run; indexed/non-indexed; centroid MS1; explicit second/minute RT normalized to seconds; float32/float64 m/z and intensity; zlib/no compression; nonempty finite equal-length arrays. MS2 rejects with `MS2_PARSING_NOT_IMPLEMENTED`; any Chromatogram rejects with `CHROMATOGRAM_PARSING_NOT_IMPLEMENTED`.
- Both fixtures complete Inspector -> Plan -> Registry -> Runner -> Writer -> Validator -> Reader. The suite contains 167 passing tests after B3.
- The adjacent 31,408,514-byte mixed MS1/MS2 sample rejects at `spectrum[2]` with `MS2_PARSING_NOT_IMPLEMENTED` in 2.582868 seconds on the final recorded run, with zero committed Blocks and no `.zp` output.
- P1-B4 may begin only with explicit MS2/precursor fixtures, complete single-precursor/selected-ion facts, atomic failure coverage, and no reinterpretation of v1 core fields.

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

Status: **completed on 2026-07-13. P1-B5 has not started.**

Completion record:

- `binary_layer/mzml_adapter.py` preserves every precursor and selected-ion count without first-item selection. For the admitted one-to-one subset it converts selected-ion m/z, explicit charge, intensity, parent `spectrumRef`, isolation target/offsets, activation CV terms, and collision energy/unit into immutable plain-Python facts.
- `RealMzmlParseTool` now creates one deterministic `PrecursorBlock` per admitted MS2. `PrecursorBlock.spectrum_id` points to the child MS2, and `SpectrumBlock.precursor_id` points back to the same precursor. MS1 has no precursor.
- The existing frozen `mzml_metadata` v1 fields carry source precursor metadata. No core Block, schema version, Writer, Reader, Validator, Runner, plan, Registry, or admission-policy change was required.
- Added a complete precursor-metadata fixture plus atomic rejection fixtures for missing/multiple precursor or selected ion, missing required scalars, explicit zero charge, negative isolation offsets, and non-finite precursor values. Existing admission tests continue to reject MS3+ and DIA.
- Indexed and non-indexed MS1/MS2 fixtures complete Inspector -> Plan -> Registry -> Runner -> Writer -> Validator -> Reader. Corrupted Spectrum-to-Precursor and Precursor-to-Spectrum references are rejected by the existing Validator.
- The final B4 suite contains 204 passing tests. Deterministic fixture regeneration is byte-identical, and every B4 precursor rejection stops before derived blocks and Writer with no `.zp` artifact.
- The adjacent 31,408,514-byte mixed sample now parses its MS1/MS2 content and rejects the complete file at `chromatogram[0]` with `CHROMATOGRAM_PARSING_NOT_IMPLEMENTED` in 3.686687 seconds on the final recorded boundary check, leaving zero committed Blocks and no `.zp` artifact.
- Chromatograms remain a whole-file capability rejection with `CHROMATOGRAM_PARSING_NOT_IMPLEMENTED`; TIC/BPC conversion belongs only to P1-B5.

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

Status: **completed on 2026-07-14.**

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

Completed evidence:

- `ParsedMzmlChromatogram` is immutable and carries source index/native ID, CV-derived TIC/BPC type, normalized and source time values, intensity values, unit/dtype/compression provenance, default length, processing reference, typed auxiliary arrays, and precursor/product-semantic flags.
- One parsed chromatogram fact set feeds admission, `mzml_metadata` v1, `mzml_auxiliary_arrays` v1, `ChromatogramBlock`, and both core `ArrayBlock` records; no Block-building reparse occurs.
- Deterministic IDs are `chromatogram_000001` and `chromatogram_000001:{time,intensity}`. Core dtype is float64 and time is seconds; native IDs remain verbatim.
- Accepted fixtures cover indexed TIC/minute/float64/zlib, non-indexed BPC/second/float32/uncompressed, and TIC+BPC with the whitelisted int64 `MS:1000786` `ms level` auxiliary array.
- Rejection fixtures cover missing arrays, decoded-length mismatch, unknown time unit, SRM, precursor/product semantics, and unknown chromatogram auxiliary arrays. Every failure leaves the Block collection empty and stops before derived Blocks, Writer, and Validator.
- Validator required a minimal change because it already checked chromatogram IDs, run/array references and array types, but did not check chromatogram array-length equality or non-negative time values. Checksum-recomputed negative tests now cover both missing references, both type errors, length mismatch, missing run, and negative time.
- Tiny end-to-end outputs validate and read back: indexed TIC 7,632 bytes, non-indexed BPC 7,659 bytes, and TIC+BPC 6,846 bytes.
- The 31,408,514-byte real sample completed the full pipeline: 2,048 spectra (997 MS1, 1,051 MS2), 1,051 precursors, 1 TIC, 4,098 core arrays, 2 extensions, and 2,379,436 peaks. Validator and Reader passed.
- Real-sample output was 78,103,277 bytes (2.4867x input). Recorded elapsed time was 12.966 s parse/candidate build, 23.092 s Writer, 15.493 s Validator, 52.002 s complete pipeline, and 1.506 s Reader summary. Python `tracemalloc` peak was 467,324,974 bytes; NumPy native allocation is not fully represented. The Writer temporary file reached approximately the final 78.1 MB before atomic replacement.
- Conversion simultaneously retains parsed tuples and candidate Block lists. These measurements prove correctness only, not production-scale performance.

### Failure rollback

Restore the pre-B5 rule that any nonempty chromatogram list rejects.

## P1-B6: real mzML scale, memory, and array-storage evaluation

### Goal

Evaluate the observed JSON output expansion, whole-candidate memory residency, Pyteomics/NumPy allocation, and the v1 whole-list array storage/read limitation before proposing any format change.

### Modify files

- Measurement scripts and evidence documents only at the start.
- Any later storage proposal requires a separate format/version review before implementation.

### Do not modify files

- Do not pre-implement compression, binary typed payloads, array-level offsets, memory mapping, or Viewer integration.
- Do not silently reinterpret `.zp` v1 arrays or claim the 31 MB result is a scalability ceiling.

### Tests

- Repeatable stage timings, process RSS and Python traced peak on multiple sizes/peak counts.
- Input/output/temp-size ratios and Reader whole-array-block costs.
- Separate parsed-model, candidate-Block, canonical-JSON, Writer, Validator, and Reader memory evidence.
- Pyteomics chromatogram/auxiliary-array compatibility drift checks.

### Acceptance

Produce an evidence-backed storage/version decision with explicit resource ceilings. No runtime format mutation belongs to the evaluation gate itself.

### Failure rollback

Retain measurements and make no format change until the version/storage decision is reviewed.

## Planned production file set

Expected new files:

- `binary_layer/mzml_schema.py`
- `binary_layer/mzml_admission.py`
- `binary_layer/tools/real_mzml.py`
- `tests/test_mzml_schema.py`
- `tests/test_real_mzml_ms1.py`
- `tests/test_real_mzml_ms2.py`
- `tests/test_real_mzml_chromatograms.py`
- `tests/test_real_mzml_roundtrip.py`

Expected existing-file changes through P1-B5: `pyproject.toml`, Inspector, PlanBuilder, Registry, Validator, test helpers, README and P1 docs.

Expected untouched for the strict v1 subset: `constants.py`, `models.py`, `blocks.py`, `writer.py`, `reader.py`, and `runner.py`. If a stage needs one of these, stop and re-review before editing.
