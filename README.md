# `.zp` binary intermediate layer — P0 prototype

This repository is an independent Python 3.11+ prototype of a mass-spectrometry Viewer conversion layer. It proves the contracts among source inspection, conversion planning, pipeline orchestration, strongly typed blocks, one writer, one reader, and one validator. It is **not** the final production high-performance binary format.

Source inspection classifies `.mzML` case-insensitively as `real_mzml`; `.raw` remains the P0 `mock_raw` path. P1-B5 registers `RealMzmlParseTool` for the strict single-run centroid MS1/MS2 plus TIC/BPC subset described below. Unsupported real files fail atomically and never fall back to the mock parser. The deterministic `mock_mzml` path remains available only through an explicitly constructed `SourceProfile(source_type="mock_mzml")` in P0 tests and examples. This prototype does not integrate Viewer, a frontend, or a database.

## Architecture

```text
input file
  -> SourceInspector -> SourceProfile
  -> PlanBuilder -> ConversionPlan
  -> PipelineRunner -> named PipelineSteps from StepRegistry
       system: FileValidate -> HashInput
       pre_conversion (RAW only): MockRawToMzml
       block_tool: RealMzmlParse or MockMzmlParse -> StringPoolBuild -> IndexBuild
       system: ZpWrite -> ZpValidate
  -> ZpWriter (the only production .zp write boundary)
  -> ZpReader / ZpValidator
```

For `real_mzml`, the fixed plan is `FileValidate -> HashInput -> real_mzml_parse -> StringPoolBuild -> IndexBuild -> ZpWrite -> ZpValidate`. The default Registry binds `real_mzml_parse` to `RealMzmlParseTool`; Registry and Runner contain no source-type or mass-spectrometry business branching.

`BaseBlockTool` only reads `PipelineContext`, creates typed blocks, and updates `context.blocks`. It cannot set `output_zp_path`, write `.zp`, invoke validation, or hide core data in metadata. `FileValidateStep`, `HashInputStep`, `ZpWriteStep`, and `ZpValidateStep` are system steps. `MockRawToMzmlTool` is a pre-conversion step, not a block tool. `PipelineRunner` only executes the plan in order and records started/completed/failed logs. `StepRegistry` only registers and retrieves names.

## Version 1 file format

The fixed header is exactly 24 bytes and uses `struct.Struct("<4sHBBQQ")`:

| Field | Size | Meaning |
|---|---:|---|
| magic | 4 | `ZPMS` |
| version | 2 | unsigned version, currently `1` |
| endianness | 1 | `1`, little-endian |
| flags | 1 | currently `0` |
| created_at | 8 | unsigned Unix epoch milliseconds |
| directory_offset | 8 | absolute offset of the trailing 8-byte directory length |

The writer then stores nine canonical UTF-8 JSON blocks in this fixed order: `global_meta`, `string_pool`, `core_runs`, `core_spectra`, `core_precursors`, `core_chromatograms`, `arrays`, `indexes`, and `extensions`. Empty chromatograms and extensions are still written and listed. Each directory entry records block name, byte offset, byte length, `json` encoding, and the lowercase SHA-256 of the exact stored block bytes.

At `directory_offset`, an 8-byte little-endian unsigned length precedes the canonical directory JSON. The directory occupies the file tail. `ZpWriter` writes a sibling `.tmp`, flushes and `fsync`s it, then atomically installs it with `os.replace`. It does not build missing indexes/string pools, repair references, or mutate business blocks.

`SpectrumBlock` contains only `mz_array_id` and `intensity_array_id`; peak values live independently in `ArrayBlock` records. The P0 `arrays` payload is explicitly a JSON list, not an object keyed by ID:

```json
[
  {"array_id": "mz_1", "array_type": "mz", "dtype": "float64", "values": [100.0]}
]
```

Every record carries a unique `array_id`, and references use that ID. Current lookup builds or scans an in-memory ID map after reading the complete list; this is logical organization by ID, not an on-disk high-performance ID index. `read_spectrum_arrays` therefore performs block-level reading rather than true single-array random I/O. Compression, binary numeric payloads, and memory mapping are later phases.

The string pool is a forward-compatible, deduplicated structure. P0 deliberately retains original string fields and does not yet replace them with string IDs.

### Frozen P0 version-1 baseline

The following contract is frozen as the **ZP format version 1 prototype baseline**:

- `ZPMS`, version `1`, little-endian value `1`, and the exact 24-byte header layout.
- `directory_offset` points to the 8-byte directory length; the declared directory JSON must end exactly at EOF.
- The nine required block names and their fixed write order.
- Canonical JSON rules: UTF-8, sorted keys, compact separators, and no NaN/Infinity output.
- A directory checksum covers the exact stored bytes of its block.
- The stable ID relationships among runs, spectra, precursors, arrays, and indexes.

Changing any item above requires an explicit format/version review; P1 must not silently reinterpret version 1.

Version-1 field conventions are:

- `SpectrumBlock.rt`, `RunBlock.start_rt`, and `RunBlock.end_rt` are seconds.
- m/z and `precursor_mz` use mass-to-charge units and may not be negative.
- Intensity arrays contain finite source-domain detector values. The format layer permits negative baseline-corrected values and never silently repairs them.
- `scan_number` and precursor `charge` are required integers in P0 v1. A real source with either value missing is not represented using `-1`, `0`, or another sentinel; P1 must make an explicit schema/version or extension decision first.
- Nullable fields such as `precursor_id` use JSON `null`, never an ad-hoc string sentinel.

The validator rejects trailing bytes after the directory, overlapping block ranges, duplicate block names, unsupported versions/endianness/encodings, malformed checksum text, and invalid directory offsets or lengths.

## Layout

```text
binary_layer/       package: models, blocks, pipeline, format I/O, validation
binary_layer/tools/ system, pre-conversion, and block-producing steps
examples/           complete mock mzML build and read-back
scripts/            .zp inspection CLI
tests/              happy-path, boundary, corruption, and reference tests
tests/fixtures/mzml deterministic P1-B1 mzML compatibility fixtures
```

## Install and verify

P1-B1 constrains Pyteomics to `>=4.7.5,<5`; P1-B5 implements the strict real-MS1/MS2 plus TIC/BPC subset below. For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python examples/build_mock_zp.py --output-dir ./output
python scripts/inspect_zp.py ./output/mock_run.zp
python scripts/inspect_zp.py ./output/mock_run.zp --spectrum-id spectrum_2
```

Without installation, running from the repository root also works because the example and inspection scripts add that root to their import path.

## Scope and future phases

- P1: real mzML ingestion, richer run/instrument metadata, and explicit schema evolution.
- P2: binary typed array payloads, array-level offsets, compression, and bounded-memory writing.
- P3: real RAW conversion adapters, multi-file/multi-run policies, recovery, and parallel conversion.
- P4: Viewer, database, frontend, BU, TopDown, and DIA integration with production migration tooling.

This prototype does not implement real RAW, general mzML conversion, BU, TopDown, DIA, Viewer integration, a database, a frontend, high-performance binary numeric arrays, compression, memory mapping, parallel conversion, or production recovery.

## P1 status

P1-A investigation and P1-B1 through P1-B8.1 are complete. P1-B8.2 has not started. P1-B1 adds deterministic accepted/rejected fixtures, pins Pyteomics to `>=4.7.5,<5`, freezes `mzml_metadata` v1 and `mzml_auxiliary_arrays` v1 schemas, and adds a parser-independent admission policy. P1-B5 completed the strict real MS1/MS2 plus TIC/BPC conversion subset. P1-B6 evaluated the v1 JSON scale limit; P1-B7 froze the design for a version-2 arrays region with a 64-byte internal Header, canonical array directory, zero alignment padding, and contiguous little-endian float64 payloads.

P1-B5 supports one local mzML file with one run, indexed or non-indexed, centroid MS1 and MS2, and zero or more TIC/BPC chromatograms. Every MS2 must have exactly one precursor and one selected ion with explicit m/z, nonzero charge, and intensity. Spectrum RT and chromatogram time accept explicit seconds or minutes and are normalized to seconds. Required arrays accept float32/float64 and zlib/no compression; they must be nonempty, finite, and aligned, with non-negative m/z and time values. Core `ArrayBlock` values are normalized to float64. Source dtype, compression, units, RT/time provenance, parent `spectrumRef`, isolation window, activation methods, and collision energy/unit are preserved in `mzml_metadata` v1. Whitelisted auxiliary arrays, currently chromatogram `MS:1000786` `ms level` int64, are preserved in `mzml_auxiliary_arrays` v1.

SRM, MRM, SIC, selected-ion-current, precursor/product chromatograms, unknown chromatogram types, profile spectra, DIA, ion mobility, MS3+, missing/multiple precursor or selected-ion structures, missing required precursor scalars, unknown auxiliary arrays, unsupported native-ID formats, missing scan numbers, ambiguous time/RT units, Numpress, and multiple runs are rejected. This is not general mzML support. The `arrays` block remains one complete JSON list and conversion keeps the parsed model plus candidate Blocks in memory; the successful 31.4 MB sample conversion is not a production-scale performance claim. Real RAW conversion, compression, binary array payloads, memory mapping, and Viewer integration remain unsupported.

P1-B6 repeated the 31,408,514-byte real sample three times. Every output was 78,103,277 bytes (2.486691x input) for 2,379,436 peaks; the median traced Python peak was 471,928,798 bytes and median process RSS peak was 1,646,055,424 bytes. The `arrays` block was 74,610,555 bytes (95.5281% of `.zp`), and current single-Spectrum array access reparses the full block. The bounded v1 prototype gate warns at 32 MiB input, 2M peaks, 80 MiB predicted output, or 1.5 GiB predicted RSS; it rejects above 64 MiB input, 5M peaks, 200 MiB output, or 4 GiB predicted RSS, subject to aggregate free-resource checks.

P1-B8.1 adds Header-first version dispatch to the public Writer, Reader, and Validator facades. The production `ZP_VERSION` and default Writer remain version 1. Explicit v2 write/read requests fail closed with operation-specific errors, and v2 validation returns one explicit not-implemented issue before v1 body parsing. The isolated standard-library Codec under `specs/zp_v2/` remains outside Registry, the conversion pipeline, and `binary_layer/`. P1-B8.1 does not implement a v2 arrays Writer, Reader, or Validator, so the product still cannot generate, open, validate, or otherwise use complete v2 `.zp` files. P1-B8.2 is the next unstarted stage.

- [P1 mzML investigation](docs/P1_MZML_INVESTIGATION.md)
- [P1-B implementation plan](docs/P1_MZML_IMPLEMENTATION_PLAN.md)
- [P1-B6 scale and memory assessment](docs/P1_B6_SCALE_MEMORY_ASSESSMENT.md)
- [P1-B6 array storage decision](docs/P1_B6_ARRAY_STORAGE_DECISION.md)
- [ZP v2 binary arrays format specification](docs/ZP_V2_BINARY_ARRAY_FORMAT_SPEC.md)
- [ZP v2 compatibility and migration design](docs/ZP_V2_COMPATIBILITY_AND_MIGRATION.md)
- [P1-B8 staged implementation plan](docs/P1_B7_IMPLEMENTATION_PLAN.md)
- [P1-B1 fixture manifest and regeneration](tests/fixtures/mzml/README.md)
