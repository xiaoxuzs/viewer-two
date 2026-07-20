# ZP v2 compatibility and migration design

Status: **P1-B8.6 complete: explicit v2 Writer, dual Reader/Validator, full compatibility Goldens, and safe offline v1-to-v2 migration are implemented. Default output remains v1.**

Date: 2026-07-14 (Asia/Shanghai)

## 1. Version dispatch

The public entry remains conceptually `ZpReader`. It reads the fixed 24-byte
Header once, checks `magic`, and dispatches only on `header.version`:

```text
version 1 -> frozen v1 implementation
version 2 -> independent v2 implementation
other     -> UNSUPPORTED_ZP_VERSION
```

Arrays encoding and arrays magic never choose or repair the version. File
extension also does not distinguish versions.

## 2. Reader architecture

Recommended production structure for P1-B8 is a facade over isolated version
implementations:

```text
ZpReader facade
|- ZpV1Reader (frozen JSON behavior)
`- ZpV2Reader (new top-level tokens and binary arrays)
```

P1-B7 creates none of these internal production classes. The v2 interface
must include `read_header`, `read_directory`, `read_block`,
`read_array_directory`, `read_array`, `read_spectrum`,
`read_spectrum_arrays`, and `read_chromatogram_arrays`.

One `ZpReader` instance may cache the parsed top-level and array directories.
It never caches all payloads, never uses a cross-instance/global cache, and
does not change checksum behavior. Cache lifetime equals Reader-instance
lifetime and invalidates before reuse if file size, high-resolution mtime, or
open-handle/file identity changes. A target read still verifies that target's
per-array checksum after a cache hit.

## 3. Writer architecture

Recommended structure is an explicit version-selecting facade over isolated
v1/v2 encoders. A caller uses `ZpWriter(format_version=1)` or
`ZpWriter(format_version=2)`, or a semantically equivalent explicit factory.
The Writer never selects a version from file size, source type, MS level, or a
resource warning. Existing calls that omit a version continue writing v1
during development and trial periods.

BlockTools continue producing the unchanged logical `ArrayBlock` values;
Writer alone chooses v1 JSON or v2 physical bytes. `RealMzmlParseTool` never
sees a format version and never builds a payload. Both versions use a sibling
temporary file, flush/`fsync`, and atomic replace; the Writer does not repair
missing blocks, indexes, pools, IDs, or references.

## 4. Validator architecture

Recommended structure is `ZpValidator` dispatching on Header version to
independent v1/v2 validation paths. The v2 path performs:

1. top-level Header, version, endianness, directory, nine ranges/order/EOF,
   encoding, and block SHA-256 checks;
2. arrays Header, internal directory, padding, continuity, and bounds checks;
3. all per-array SHA-256, float64, numeric, reference, type, and paired-length
   checks.

Full validation includes top-level arrays SHA-256 and every per-array
checksum. Random Reader access validates only the selected array and is not a
substitute for full validation.

## 5. Frozen v1 principles

v1 arrays remain a canonical JSON list. Legal v1 files remain readable by the
same semantics and error behavior. The implementation does not translate a v1
file into an in-memory v2 physical representation and call that native v1
reading. No v1 block field, ID, array type, RT unit, checksum, or encoding
token is relaxed. v1 tests and fixtures remain permanently.

## 6. Version/encoding matrix

The requested semantic matrix and the observed repository bytes differ in one
literal: the current v1 Writer and `SUPPORTED_ENCODINGS` use `"json"`, not
`"utf-8-json"`. Compatibility must follow actual bytes:

| Header version | arrays directory encoding | Result |
|---:|---|---|
| 1 | `json` (frozen v1 token; semantically canonical UTF-8 JSON) | v1 read |
| 2 | `zp-arrays-v2` | v2 read |
| 1 | `zp-arrays-v2` | `ARRAYS_ENCODING_VERSION_MISMATCH` |
| 2 | `json` or `utf-8-json` | `ARRAYS_ENCODING_VERSION_MISMATCH` |
| unknown | any | `UNSUPPORTED_ZP_VERSION` |

For v2, the other eight entries use the new explicit token
`utf-8-json`. Any other arrays encoding is
`UNSUPPORTED_ARRAYS_ENCODING`. This records the byte-level repository fact
without weakening the required rejection cases or guessing from encoding.

## 7. API compatibility

Existing logical return types can remain stable while version-specific byte
parsers remain separate. v1 methods keep their current cost and semantics.
v2 `read_array(array_id)` returns the same logical float64 values after a
target-only disk read. Unknown versions and mismatches fail before block
decoding. Public callers do not pass a read version; only the Header decides.

## 8. Default version strategy

During v2 implementation and trial, omitted Writer version means v1. v2 is
explicit. After separate performance, corruption, migration, and release
acceptance, changing the default is a new decision. P1-B8 must not silently
perform that switch. A v1 limit rejection recommends explicit v2 rather than
secretly emitting a different format.

## 9. v1 resource admission

Until v2 is production-ready, P1-B6 gates remain: warn at 32 MiB input, 2M
peaks, 80 MiB predicted output, or 1.5 GiB predicted RSS; reject above 64 MiB,
5M peaks, 200 MiB output, or 4 GiB predicted RSS, with aggregate free-resource
checks. These are v1 prototype gates, not format limits or evidence that v2 is
already available.

## 10. v1-to-v2 migration

The production `migrate_v1_to_v2` API and
`python -m binary_layer.migration` CLI perform:

```text
capture source identity/hash -> require full valid v1
-> parse eight canonical JSON blocks and stream the v1 arrays list once
-> spool exact little-endian float64 payload bytes and O(array_count) metadata
-> encode arrays as zp-arrays-v2 and the other blocks as v2 canonical JSON
-> flush/fsync a sibling temporary file -> fully validate temporary v2
-> compare exact version-neutral logical fingerprints
-> recheck source identity/hash -> atomically replace the absent target -> report
```

It never invokes BlockTools or `RealMzmlParseTool`, reparses mzML, guesses
missing data, or changes business semantics.

## 11. Logical equality

Compare GlobalMeta logical fields, Run, Spectrum, Precursor, Chromatogram,
Index, Extension, every array ID/type/count/value, and every reference. Array
values compare exact decoded binary64 values in order. Scan, native ID, RT,
precursor values, chromatogram fields, business IDs, and references may not
change.

Allowed physical differences are Header version, arrays encoding
and checksums, block offsets, `directory_offset`, file size, and generator
information only when an existing explicit field legitimately carries it.

## 12. Atomic write and no in-place migration

Source is opened read-only and is never a target. The caller provides a
distinct output path. Reject equal or
filesystem-equivalent source/target paths. Write a sibling target temporary,
flush, `fsync` its file, optionally sync the parent directory where supported,
validate and fingerprint it, then `os.replace` it to the new target. Existing
targets are always rejected; there is no overwrite option. Source is never
replaced.

## 13. Failure recovery

Before replace, all validation, checksum, fingerprint, and source-change gates
have completed. Any failure or interruption removes only tool-owned spool,
validation alias, and temporary target paths; the source remains byte-identical
and the absent target remains absent. The structured CLI result records a
stable stage/code and never prints a traceback for controlled errors.

## 14. Golden fixtures

`specs/zp_v2/fixtures/valid_arrays_v2.bin` and
`valid_empty_arrays_v2.bin` freeze the arrays subformat. `manifest.json`
records exact hashes, sizes, offsets, entries, checksums, and values. They are
independently unpacked by tests; the Codec's own roundtrip is insufficient.
P1-B8.5 freezes full/minimal v1/v2 complete-file Goldens. P1-B8.6 reuses each
v1/v2 pair as a migration Golden: the migrated output must equal the v2 file
byte for byte while the v1 SHA-256 remains unchanged. The dedicated Manifest
and unified gate are under `specs/zp_migration/`. The 31 MB gate additionally
runs one full Reader-to-Writer reference measurement outside the production
path and requires streaming conversion RSS to remain at most 80% of that
reference peak.

## 15. Viewer future integration

Viewer integration occurs only after production Reader/Validator support and
large-file acceptance. Viewer calls logical Reader APIs and does not parse
arrays bytes, infer the version, or depend on reference fixtures. Capability
negotiation should expose v1/v2 read support and target-only array access.
P1-B7 and P1-B8 do not make Viewer depend on v2.

## 16. Release stages

1. **Development:** default Writer v1; v2 explicit; Reader/Validator dual;
   Viewer independent.
2. **Trial-ready components:** retain small v1 regression fixtures/gates;
   explicit v2 and offline one-file migration are available, but default-v2
   release still requires later gates.
3. **Default candidate:** changing Writer default requires separate acceptance;
   P1-B8 cannot do it automatically.
4. **v1 maintenance:** Reader/Validator continue v1 indefinitely; v1 Writer
   retirement is separate; v1 tests/Golden fixtures are never deleted.

## 17. Rollback strategy

Because the default remains v1, rollback disables explicit v2 creation while
retaining safe Header rejection or the last accepted dual Reader. Files
already written as v2 are not relabeled; they are opened only by an accepted
v2 Reader or regenerated from their preserved source/v1 artifact. Migration
never destroys the rollback source. Each P1-B8 stage has a file-level rollback
point in the implementation plan.

## 18. Test matrix

Required axes include v1/v2 Header; correct/mismatched/unknown encoding;
empty/nonempty arrays; every Header field and range; canonical/noncanonical
directory; ID Unicode ordering/duplicates; zero/nonzero lengths; gaps,
overlaps, truncation, trailing data; checksums; numeric constraints; reference
and pair lengths; resource limits before reads/allocation; target-only random
reads; cache invalidation; migration success/failure/atomicity; logical equality;
v1 regression; and realistic large mzML v1/v2 performance.

## 19. Handling incompatible future changes

A changed core field requires a `ZP_VERSION` review. A changed arrays Header,
directory field meaning, dtype/encoding semantics, checksum coverage, or
offset base requires an arrays schema or top-level version review and new
fixtures. Compression may not reuse `raw-le`; a new explicit encoding alone is
insufficient until its limits and compatibility dispatch are reviewed. No
Reader attempts best-effort repair.

## 20. Open questions deferred beyond P1-B8.6

- production Writer streaming/bounded-memory handoff for new conversions
  without letting a BlockTool write bytes (migration already has its own
  bounded v1 arrays path);
- whether v2's first production limits need adjustment after larger samples;
- whether internal lookup uses binary search, an instance map, or both;
- exact file identity abstraction for cache invalidation on every platform;
- whether later compression merits arrays schema 3 or top-level ZP v3;
- when, if ever, the default Writer changes or v1 writing is retired.

`MigrationResult` contains source/target paths, versions, SHA-256, sizes,
logical fingerprints, block/array/value counts, validation and conversion
timings, source/target/conversion RSS, temporary disk, disk preflight,
single-scan/max-live-array instrumentation, and payload spool/copy bytes. The
31,408,514-byte acceptance run migrated 4,098 arrays and 4,762,968 values to a
42,559,842-byte target byte-identical to direct Writer v2, comparing all 4,098
array hashes. Default Writer/Pipeline output and `ZP_VERSION` remain 1; batch
migration, Viewer integration, and the B8.8 performance release gate remain
future work.
