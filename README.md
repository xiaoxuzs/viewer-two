# `.zp` binary intermediate layer — P0 prototype

This repository is an independent Python 3.11+ prototype of a mass-spectrometry Viewer conversion layer. It proves the contracts among source inspection, conversion planning, pipeline orchestration, strongly typed blocks, one writer, one reader, and one validator. It is **not** complete ZP v2 product support.

Source inspection classifies `.mzML` case-insensitively as `real_mzml`, `.raw` case-insensitively as `real_thermo_raw`, a content-validated single-run Viewer-compatible bundle as `real_top_down_bundle`, and a uniquely paired single-run Thermo DIA mzML plus DIA-NN 2.0 Parquet directory as `real_dia_result_bundle`. P1-B5 registers `RealMzmlParseTool` for the strict single-run centroid MS1/MS2 plus TIC/BPC subset described below. P2-A1 routes Thermo RAW through ThermoRawFileParser indexed mzML and then reuses that same strict mzML Tool. P2-B1 reuses either spectrum path and adds versioned Top-Down business Extensions. P2-C2 uses an explicit DIA admission mode, writes acquisition windows to core `isolation_window` precursors, and writes all Bottom-Up identification/quantification entities only to versioned Extensions. Unsupported real files fail atomically and never fall back to a mock parser. The deterministic `mock_mzml` and `mock_raw` paths remain available only through explicitly constructed `SourceProfile` values in tests and examples. This project does not integrate the live Viewer application, a frontend, or a database.

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

For `real_top_down_bundle`, the named `real_top_down` BlockTool delegates core
spectrum creation to the existing real mzML or Thermo RAW Tool, associates
PrSMs to core Spectra by explicit scan/native identity, and appends six
schema-version-1 Top-Down Extensions. It never writes `.zp` or invokes a
Validator. See [P2-B1 Top-Down conversion](docs/P2_B1_TOP_DOWN.md).

For `real_mzml`, the fixed plan is `FileValidate -> HashInput -> real_mzml_parse -> StringPoolBuild -> IndexBuild -> ZpWrite -> ZpValidate`. The default Registry binds `real_mzml_parse` to `RealMzmlParseTool`; Registry and Runner contain no source-type or mass-spectrometry business branching.

For `real_thermo_raw`, the fixed plan is `FileValidate -> HashInput -> real_thermo_raw_parse -> StringPoolBuild -> IndexBuild -> ZpWrite -> ZpValidate`. `RealThermoRawParseTool` invokes ThermoRawFileParser with an argument list and `shell=False`, requires a usable indexed mzML output, delegates all mzML parsing and Admission to `RealMzmlParseTool`, and adds only the versioned `thermo_raw_conversion_metadata` provenance Extension. The public entry point is `binary_layer.service.convert_source_to_zp`; it defaults to v1 and accepts explicit `format_version=2`.

```python
from pathlib import Path

from binary_layer import ConversionOptions
from binary_layer.service import convert_source_to_zp

result = convert_source_to_zp(
    source_path=Path("sample.raw"),
    target_path=Path("sample.zp"),
    format_version=2,
    options=ConversionOptions(
        converter_path=Path("ThermoRawFileParser.exe"),
        temporary_directory=Path("external-work/intermediate"),
        keep_intermediate=False,
        timeout_seconds=3600,
    ),
)
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
- m/z, `precursor_mz`, `isolation_lower_mz`, and `isolation_upper_mz` use mass-to-charge units and may not be negative.
- Intensity arrays contain finite source-domain detector values. The format layer permits negative baseline-corrected values and never silently repairs them.
- `scan_number` remains a required integer. A missing scan is never represented using `-1`, `0`, or another sentinel.
- `core_precursors` has two logical record kinds. A missing `precursor_kind` is the legacy spelling of `selected_precursor`, which requires finite non-negative `precursor_mz`, finite `intensity`, and a positive integer `charge`. An explicit `isolation_window` instead requires finite bounds with `isolation_lower_mz < isolation_upper_mz`; `precursor_mz`, `intensity`, and `charge` are null because the acquisition window is not one selected precursor. Non-null DIA charge and all other conflicting combinations reject.
- Nullable fields such as `precursor_id` use JSON `null`, never an ad-hoc string sentinel.

The three P2-C1.1 fields `precursor_kind`, `isolation_lower_mz`, and
`isolation_upper_mz` are optional at the record-serialization boundary. The
canonical Writer omits them when they are null, so existing DDA v1/v2 bytes
remain unchanged. `ZpReader.read_precursors()` exposes
`effective_precursor_kind`, which returns `selected_precursor` for those
legacy records. This is a logical core-block schema extension only: the Header,
directory, nine blocks/order, v1/v2 arrays encodings, checksums, offsets,
alignment, Writer version dispatch, `ZP_VERSION`, and default v1 output do not
change.

The validator rejects trailing bytes after the directory, overlapping block ranges, duplicate block names, unsupported versions/endianness/encodings, malformed checksum text, and invalid directory offsets or lengths.

## Explicit version-2 Writer, Reader, and Validator

P1-B8.2 adds production writing for the frozen ZP v2 arrays layout. An explicit
`ZpWriter().write(target, blocks, format_version=2)` writes the same nine
logical blocks, uses canonical `utf-8-json` for the eight non-arrays blocks,
and writes `arrays` as `zp-arrays-v2`: a 64-byte arrays Header, canonical
internal directory, zero alignment padding, and contiguous little-endian
float64 payloads. Arrays are ordered by UTF-8 `array_id`, written in bounded
chunks using a two-pass checksum/layout strategy, and protected by both
per-array and whole-block SHA-256. The production Writer is independent of the
reference Codec under `specs/zp_v2/`.

P1-B8.3 adds production v2 reading without changing the default Writer or
either Pipeline: `read_array`, `read_spectrum_arrays`, and
`read_chromatogram_arrays` seek to only the requested binary64 payloads and
verify their per-array SHA-256. `read_arrays` and `read_block("arrays")` are
the explicit full-decode paths; they enforce a conservative decoded-memory
budget and verify both the whole arrays-block checksum and every per-array
checksum. Reader instances cache only the strict canonical top-level and
internal directories, keyed by file identity; payloads and decoded values are
never cached.

P1-B8.4 adds complete production v2 validation behind the existing
`ZpValidator` facade. It strictly validates the Header, canonical trailing
directory, all nine top-level checksums, all eight JSON blocks, the binary
arrays Header/directory/padding, every per-array checksum and float64 value,
and all logical references/counts. The arrays payload is scanned once in
bounded chunks while the whole-block checksum, per-array checksum, and numeric
semantics are evaluated together; the complete payload and decoded values are
not retained. The default Writer and both Pipelines still produce v1,
`ZP_VERSION` remains `1`, and v2 is not the default released format.
P1-B8.5R aligned the v1 Validator's known
`mzml_auxiliary_arrays` Extension schema and owner-reference checks with the
existing v2 semantics. P1-B8.5R2 aligned v1 GlobalMeta run, Spectrum,
Chromatogram, and Array count validation with v2 by comparing already parsed
record counts. P1-B8.5R3A aligned the v1 Run `spectrum_count` and
`chromatogram_count` fields with actual records grouped by `run_id` using one
linear aggregation per record class. P1-B8.5R3B aligned the five required
StringPool reference fields using one StringPool set plus one ordered pass over
Run, Spectrum, and Chromatogram records. P1-B8.5R3C aligned bidirectional
Precursor links, MS1/MS2 ownership, and exactly-one-MS2 usage using hash
indexes and ordered record passes. None of these corrections changes format
bytes. P1-B8.5 completed the complete compatibility rerun: deterministic full
and minimal v1/v2 Goldens are frozen behind an independent standard-library
inspector, exact unified logical-model comparisons, Writer/Reader/Validator
and version/encoding matrices, 22 cross-version domain-error cases, corruption
matrices, real MS1-only/MS1/MS2/TIC/BPC comparisons, and a bounded 31.4 MB
sample gate. The known parity corrections are permanent failure-Fixture
regressions. The default Writer and Pipeline remain v1 and `ZP_VERSION` remains
`1`; no default-v2 release, B8.8 performance release gate, or Viewer
integration exists. P1-B8.6 adds an explicit offline migration API/CLI:
`migrate_v1_to_v2(source, target)` and `python -m binary_layer.migration
--input source.zp --output target.zp --json`. It fully validates the read-only
v1 source, streams the canonical v1 arrays list one record at a time into one
float64 payload spool, writes and fully validates a sibling temporary v2 file,
compares exact production logical fingerprints, rechecks source identity and
SHA-256, and only then commits with `os.replace`. Existing targets, in-place
paths, aliases, invalid sources, and file changes are rejected. Full/Minimal
migration Goldens, three real mzML categories, 28 failure conditions, and the
31,408,514-byte sample are gated; the large migrated file is byte-identical to
direct Writer v2 output. The large gate also runs the forbidden-as-production
full `Reader.read_arrays()` -> `BlockCollection` -> Writer path once as a
reference: the streaming conversion RSS was 37.1% of that reference peak on
the recorded run. P1-B8.6 is complete; P1-B8.7 has not started.

## P2-B2 TopPIC/TopFD interpretation generation

`convert_source_to_zp(...)` now recognizes a single-run directory containing
one mzML spectrum source plus paired TopPIC `*_toppic_prsm.xml` and TopFD
`*_ms2.msalign` inputs as `real_top_down_intermediate_bundle`. Discovery is
bounded to the root and its immediate children. Existing `prsm*.js` bundles
retain priority and continue to use `real_top_down_bundle`; a lone mzML remains
ordinary `real_mzml` unless `requested_conversion_kind="top_down"`, in which
case it fails with `TOP_DOWN_INTERPRETATION_INPUTS_MISSING`.

The `real_top_down_intermediate_parse` block tool runs the configured
`prsmup.py` in a unique isolated directory with an argument list,
`shell=False`, captured output, timeout, full XML PrSM count as `--limit`, and
no writes to the source bundle. Generated `prsm*.js` files are admitted by the
existing P2-B1 `TopDownAdapter`; no second Top-Down entity schema or JS parser
was added. The output includes `top_down_interpretation_provenance` v1, and
`get_top_down_interpretation_provenance()` exposes its stable, path-free input,
script, Python, and generated-artifact summaries.

The production path fails closed when generated PrSM IDs do not cover the XML
IDs or when generated Modification counts differ from XML `mass_shift` counts,
globally or for any individual PrSM. P2-B2.1 fixed the inspected `prsmup.py`
first-mass-shift truncation at the generator. The isolated PXD045330 diagnostic
now has 44/44 PrSMs and 43/43 Modifications with complete per-PrSM coverage;
P2-B2 formal real-data acceptance remains blocked because no different complete
intermediate bundle was found in the bounded data roots. See
[the P2-B2 report](docs/P2_B2_TOP_DOWN_INTERPRETATION.md).

## P2-C2 DIA-NN Bottom-Up production conversion

`convert_source_to_zp(...)` recognizes a directory only when it contains one
`all_report.parquet` (preferred) or `target_report.parquet` and exactly one
mzML whose normalized file/run identity equals the report's single `Run`
value. Multiple report roles, multiple runs, ambiguous spectra, and unmatched
runs fail with stable codes. The fixed plan contains one registered
`real_dia_result` block tool; that tool never writes or validates `.zp`.

The DIA path preserves the complete admitted mzML run. Every MS2 owns exactly
one core `isolation_window` precursor with absolute lower/upper m/z bounds and
null selected-precursor m/z, charge, and intensity. Identification charge and
`Precursor.Mz` remain in `bottom_up_identifications` v1. DIA-NN Parquet is read
in RecordBatches, all 69 frozen columns have an explicit typed mapping, and
the complete primary source table is retained as columnar batch records in
metadata. Viewer-compatible association uses report RT in minutes, closed
absolute isolation bounds, no m/z tolerance, a 0.5-minute maximum delta, and
nearest RT followed by scan-number tie-breaking.

The conditional v1 Extensions are `bottom_up_metadata`, identifications,
peptides, proteins, protein groups, modifications, fragment matches, and
quantification. Unsafe PFMB pickle inputs are never deserialized; they are
hash/preserve-only sources and fragment support is reported as unavailable.
`BottomUpReader` hides v1/v2 physical differences, while
`BottomUpExtensionValidator` is composed after physical and Top-Down
validation by `validate_zp(...)`. P2-C2.1 makes `validate_zp(..., mode="quick")`
the explicit daily integrity path and keeps `mode="deep"` for full scientific
semantics plus deterministic acceptance-certificate generation. The initial
Bottom-Up Reader still indexes a fully decoded Extension in memory; its public
query contract is stable, but v2's 1.097 GB canonical JSON remains a measured
deep-validation and random-access boundary that requires a future columnar v3
Extension. The P2-C2 functional and scientific chain is valid, but its original
performance acceptance remains failed. See [the P2-C2 report](docs/P2_C2_DIA_NN_BOTTOM_UP.md)
and [the P2-C2.1 performance report](docs/P2_C2_1_PERFORMANCE.md).

## Layout

```text
binary_layer/       package: models, blocks, pipeline, format I/O, validation
binary_layer/tools/ system, pre-conversion, and block-producing steps
examples/           complete mock mzML build and read-back
scripts/            .zp inspection CLI
tests/              happy-path, boundary, corruption, and reference tests
tests/fixtures/mzml deterministic P1-B1 mzML compatibility fixtures
specs/zp_migration offline migration Goldens, frozen hashes, and release gate
```

## Install and verify

P1-B1 constrains Pyteomics to `>=4.7.5,<5`; P2-C2 uses PyArrow `>=23,<24` for bounded DIA-NN Parquet batches. For development:

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
- P2: later compression, memory mapping, and larger-scale bounded-memory conversion work.
- P3: additional vendor RAW adapters, multi-file/multi-run policies, recovery, and parallel conversion.
- P4: Viewer, database, frontend, BU, TopDown, and DIA integration with production migration tooling.

This project implements Thermo RAW conversion through an external
ThermoRawFileParser, P2-B1 precomputed Top-Down bundles, and the fail-closed
P2-B2 TopPIC/TopFD interpretation path described above. P2-C2 adds the formal
single-run Thermo DIA mzML plus DIA-NN 2.0 Parquet Bottom-Up path. It does not
implement direct RAW parsing, Bruker/Agilent `.d`, classic DDA search results,
live Viewer integration, a database, a frontend, batch migration, compression,
memory mapping, parallel conversion, or production recovery.

## P1 status

P1-A investigation and P1-B1 through P1-B8.6 are complete, including
P1-B8.5R/R2/R3A/R3B/R3C, the final compatibility/Golden rerun, and the safe
offline v1-to-v2 migration tool; P1-B8.7 has not started. P1-B1 adds
deterministic accepted/rejected fixtures, pins Pyteomics to `>=4.7.5,<5`,
freezes `mzml_metadata` v1 and `mzml_auxiliary_arrays` v1 schemas, and adds a
parser-independent admission policy. P1-B5 completed the strict real MS1/MS2
plus TIC/BPC conversion subset. P1-B6 evaluated the v1 JSON scale limit;
P1-B7 froze the design for a version-2 arrays region with a 64-byte internal
Header, canonical array directory, zero alignment padding, and contiguous
little-endian float64 payloads.

P1-B5 supports one local mzML file with one run, indexed or non-indexed, centroid MS1 and MS2, and zero or more TIC/BPC chromatograms. Every MS2 must have exactly one precursor and one selected ion with explicit m/z, nonzero charge, and intensity. Spectrum RT and chromatogram time accept explicit seconds or minutes and are normalized to seconds. Required arrays accept float32/float64 and zlib/no compression; they must be nonempty, finite, and aligned, with non-negative m/z and time values. Core `ArrayBlock` values are normalized to float64. Source dtype, compression, units, RT/time provenance, parent `spectrumRef`, isolation window, activation methods, and collision energy/unit are preserved in `mzml_metadata` v1. Whitelisted auxiliary arrays, currently chromatogram `MS:1000786` `ms level` int64, are preserved in `mzml_auxiliary_arrays` v1.

SRM, MRM, SIC, selected-ion-current, precursor/product chromatograms, unknown chromatogram types, profile spectra, DIA in the ordinary DDA source type, ion mobility, MS3+, missing/multiple precursor or selected-ion structures, missing required precursor scalars, unknown auxiliary arrays, unsupported native-ID formats, missing scan numbers, ambiguous time/RT units, Numpress, and multiple runs are rejected. P2-C2's separately inspected DIA result bundle uses an explicit DIA admission mode and does not relax this DDA policy. This is not general mzML support. Thermo RAW conversion inherits the DDA policy and never repairs or drops rejected facts. Direct RAW parsing, other vendor RAW formats, compression, memory mapping, and Viewer integration remain unsupported.

P1-B6 repeated the 31,408,514-byte real sample three times. Every output was 78,103,277 bytes (2.486691x input) for 2,379,436 peaks; the median traced Python peak was 471,928,798 bytes and median process RSS peak was 1,646,055,424 bytes. The `arrays` block was 74,610,555 bytes (95.5281% of `.zp`), and current single-Spectrum array access reparses the full block. The bounded v1 prototype gate warns at 32 MiB input, 2M peaks, 80 MiB predicted output, or 1.5 GiB predicted RSS; it rejects above 64 MiB input, 5M peaks, 200 MiB output, or 4 GiB predicted RSS, subject to aggregate free-resource checks.

P1-B8.1 added Header-first version dispatch to the public Writer, Reader, and Validator facades. P1-B8.2 implements explicit v2 writing with preflight resource limits, atomic replacement, byte-identical P1-B7 Golden arrays, and independent full-file tests. P1-B8.3 implements strict v2 directory parsing, bounded full decoding, and target-only Array/Spectrum/Chromatogram reads with per-instance identity-bound directory caches. P1-B8.4 implements independent full-file v2 validation with deterministic issues, strict schemas, bounded single-pass arrays scanning, both checksum levels, numeric semantics, file-change detection, and cross-block relationships. P1-B8.5R adds the corresponding known-Extension schema and owner-reference validation to v1 while preserving deterministic Extension/array issue order and linear owner lookup. P1-B8.5R2 adds the missing v1 GlobalMeta count checks in frozen field order using only existing list lengths; it adds no file I/O, JSON parse, checksum pass, arrays payload scan, or peak traversal. P1-B8.5R3A adds the missing v1 Run-owned `spectrum_count` and `chromatogram_count` checks using one per-record-class aggregation and O(1) lookup per Run field. P1-B8.5R3B adds the five required StringPool reference checks using one set construction and one ordered pass over the already parsed Run, Spectrum, and Chromatogram records. P1-B8.5R3C adds the missing v1 bidirectional Precursor, MS1/MS2 ownership, and exactly-one-MS2 usage checks with hash lookup and stable Spectrum-then-Precursor issue order. P1-B8.5 freezes deterministic complete/minimal v1/v2 Goldens and their Manifest, independently inspects both physical layouts, proves exact unified logical equality, and permanently gates the known domain corrections plus Writer/Reader/Validator, encoding, corruption, real-Fixture, and 31.4 MB compatibility matrices. P1-B8.6 implements non-in-place, no-overwrite v1-to-v2 migration with mandatory source/target validation, one bounded arrays scan, one payload spool, exact per-array and document fingerprints, source-change detection, and atomic commit. Its Full/Minimal and real-Fixture outputs are byte-identical to the existing v2 Writer, and its 31.4 MB gate compares all 4,098 array hashes. The production `ZP_VERSION`, default Writer, and Pipeline output remain version 1. The standard-library inspectors and release gates under `specs/` remain outside Registry and the conversion pipeline. The B8.8 performance release gate, default-v2 release, batch migration, and Viewer integration remain incomplete; P1-B8.7 has not started.

The restored 31,408,514-byte real mzML B8.3 baseline produced a 42,559,842-byte explicit v2 file. Its cached target Spectrum read consumed exactly 37,264 bytes of selected array payload and zero unrelated payload bytes; timing results are recorded in the staged implementation plan and are not a production performance claim.

The B8.4 full-validation gate produced a 42,559,978-byte temporary v2 file
from the same source: 4,098 arrays, 4,762,968 numeric values, and a
38,103,744-byte payload. Validation returned `valid=True`, zero issues, and
nine checked blocks. Payload bytes read equaled payload length exactly,
`payload_scan_count=1`, and the largest payload read was 28,992 bytes with a
256 KiB configured chunk. The measured 246.510 s under `tracemalloc`,
47,894,000-byte traced peak, and 384,163,840-byte sampled RSS peak are one-run
implementation baselines, not production performance claims; the temporary
large file was removed.

- [P1 mzML investigation](docs/P1_MZML_INVESTIGATION.md)
- [P2-B1 Viewer-compatible Top-Down conversion](docs/P2_B1_TOP_DOWN.md)
- [P2-B2 TopPIC/TopFD interpretation report](docs/P2_B2_TOP_DOWN_INTERPRETATION.md)
- [P2-C2 DIA-NN Bottom-Up production report](docs/P2_C2_DIA_NN_BOTTOM_UP.md)
- [P2-C2.1 large DIA performance remediation](docs/P2_C2_1_PERFORMANCE.md)
- [P1-B implementation plan](docs/P1_MZML_IMPLEMENTATION_PLAN.md)
- [P1-B6 scale and memory assessment](docs/P1_B6_SCALE_MEMORY_ASSESSMENT.md)
- [P1-B6 array storage decision](docs/P1_B6_ARRAY_STORAGE_DECISION.md)
- [ZP v2 binary arrays format specification](docs/ZP_V2_BINARY_ARRAY_FORMAT_SPEC.md)
- [ZP v2 compatibility and migration design](docs/ZP_V2_COMPATIBILITY_AND_MIGRATION.md)
- [P1-B8.6 migration gate](specs/zp_migration/README.md)
- [P1-B8 staged implementation plan](docs/P1_B7_IMPLEMENTATION_PLAN.md)
- [P1-B1 fixture manifest and regeneration](tests/fixtures/mzml/README.md)
