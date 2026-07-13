# `.zp` binary intermediate layer — P0 prototype

This repository is an independent Python 3.11+ prototype of a mass-spectrometry Viewer conversion layer. It proves the contracts among source inspection, conversion planning, pipeline orchestration, strongly typed blocks, one writer, one reader, and one validator. It is **not** the final production high-performance binary format.

The two accepted inputs are `mock_mzML` (`.mzML`, case-insensitive) and `mock_RAW` (`.raw`, case-insensitive). Both paths generate deterministic mock spectra and arrays; neither parser reads real mzML or RAW content. Future Viewer imports are intended to enter `.zp` first, but this P0 does not integrate Viewer, a frontend, or a database.

## Architecture

```text
input file
  -> SourceInspector -> SourceProfile
  -> PlanBuilder -> ConversionPlan
  -> PipelineRunner -> named PipelineSteps from StepRegistry
       system: FileValidate -> HashInput
       pre_conversion (RAW only): MockRawToMzml
       block_tool: MockMzmlParse -> StringPoolBuild -> IndexBuild
       system: ZpWrite -> ZpValidate
  -> ZpWriter (the only production .zp write boundary)
  -> ZpReader / ZpValidator
```

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
```

## Install and verify

The runtime has no third-party dependency. For development:

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

P0 does not implement real RAW, real mzML, BU, TopDown, DIA, Viewer integration, a database, a frontend, high-performance binary numeric arrays, compression, memory mapping, parallel conversion, or production recovery.
