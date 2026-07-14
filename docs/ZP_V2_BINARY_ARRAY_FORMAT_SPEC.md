# ZP v2 binary array format specification

Status: **P1-B7 frozen design and reference-codec contract; production v2 is not implemented.**

Date: 2026-07-14 (Asia/Shanghai)

## 1. Status and scope

This document freezes one design: a ZP version-2 file keeps the existing
24-byte top-level Header and nine logical blocks, while the single `arrays`
block becomes an internal JSON directory followed by contiguous raw
little-endian float64 payloads. It specifies bytes, validation, safety limits,
random access, and cross-language behavior. It does not change
`ZP_VERSION=1`, any production class, or the frozen v1 format.

The reference implementation under `specs/zp_v2/` handles only an arrays
block. It is specification evidence, not product support for v2 `.zp` files.

## 2. v1 problem evidence

P1-B6 measured a 31,408,514-byte mzML becoming a 78,103,277-byte v1 `.zp`
(2.486691x). The arrays block was 74,610,555 bytes, 95.5281% of the file,
and the whole output averaged 32.8243 bytes per peak. Process RSS peaked at
1.59-1.72 GB. One Spectrum array read took about 1.5 seconds; random100 took
160.94 seconds and repeat100 took 154.50 seconds because each call reparsed
the complete JSON arrays list. Raw float64 was exact and materially smaller
and faster; float32 changed 2,048 measured values.

## 3. Design goals

- preserve one movable `.zp` artifact and all stable logical array IDs;
- make one-array disk access proportional to that array plus directories;
- use deterministic, language-neutral bytes;
- retain float64 scientific values without the measured float32 loss;
- retain whole-block and per-array corruption detection;
- reject ambiguity, unsupported encodings, and hostile length declarations;
- keep BlockTools domain-only and the physical encoding Writer-owned.

Compression, mmap APIs, streaming production writes, auxiliary core arrays,
and changed business models are not goals of the initial v2 subformat.

## 4. Frozen invariants

The top-level magic, Header shape, nine names/order, trailing directory model,
canonical JSON rules, ID relations, and seconds convention remain. Header
version is the first dispatch key. A v1 parser never interprets v2 bytes, and
a v2 parser never repairs a Header/encoding mismatch from arrays magic.

## 5. Top-level Header

The Header remains exactly 24 bytes:

```text
struct <4sHBBQQ
magic             bytes[4] = b"ZPMS"
version           uint16   = 2
endianness        uint8    = 1
flags             uint8    = 0
created_at        uint64   = Unix epoch milliseconds
directory_offset  uint64   = absolute offset of directory_length
```

Repository evidence confirms the existing little-endian code is `1` in
`binary_layer/constants.py`. P1-B7 defines `version=2` only in this
specification; production `ZP_VERSION` remains `1`.

## 6. Nine top-level logical blocks

The exact order remains:

```text
global_meta, string_pool, core_runs, core_spectra, core_precursors,
core_chromatograms, arrays, indexes, extensions
```

For v2, the eight non-arrays blocks use canonical UTF-8 JSON and directory
`encoding="utf-8-json"`; `arrays` uses `encoding="zp-arrays-v2"`. Exactly one
entry exists for every name, including empty `core_chromatograms` and
`extensions`.

The frozen production v1 Writer currently emits the literal token
`encoding="json"` for all nine blocks. That observed token remains a v1-only
legacy byte contract; it is not silently rewritten to `utf-8-json`.

## 7. Top-level directory and checksum

At `directory_offset`, an unsigned little-endian `uint64 directory_length` is
followed by canonical directory JSON. It must end exactly at EOF. Every entry
has exactly `block_name`, `offset`, `length`, `encoding`, and `checksum`.
Checksums are 64-character lowercase SHA-256 hex over the exact raw block
bytes. The v2 arrays top-level checksum covers its 64-byte Header, directory,
zero padding, and all payload bytes.

Canonical JSON is UTF-8 produced with:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"),
           ensure_ascii=False, allow_nan=False).encode("utf-8")
```

## 8. Arrays block overview

```text
offset 0                     64-byte Arrays Header
offset 64                    canonical UTF-8 JSON Array Directory
directory end                zero padding
payload_offset (align8)      contiguous little-endian float64 payload
payload end                  exact end of arrays block
```

There is one arrays region and one internal directory. Per-array chunks are
not an alternative wire layout inside version 2; they remain a rejected
fallback from P1-B6, not part of this specification.

## 9. 64-byte Arrays Header

The exact struct is `<8sHBBIQQQQ16s>` and its size is 64.

| Offset | Size | Field | Type | Required value/meaning |
|---:|---:|---|---|---|
| 0 | 8 | magic | bytes | `b"ZPARRV2\0"` |
| 8 | 2 | schema_version | uint16 | `2` |
| 10 | 1 | endianness | uint8 | `1` (little-endian) |
| 11 | 1 | flags | uint8 | `0` |
| 12 | 4 | entry_count | uint32 | number of entries |
| 16 | 8 | directory_offset | uint64 | exactly `64` |
| 24 | 8 | directory_length | uint64 | exact JSON byte length |
| 32 | 8 | payload_offset | uint64 | `align8(64 + directory_length)` |
| 40 | 8 | payload_length | uint64 | sum of all `byte_length` |
| 48 | 16 | reserved | bytes | sixteen zero bytes |

Nonzero flags/reserved, other endianness, a non-64 directory offset, an
unaligned or non-derived payload offset, overflow, or trailing bytes reject.

## 10. Internal directory schema

The top-level object contains exactly one field, `entries`. Unknown or missing
top-level fields reject. Each entry contains exactly these eight fields:

```json
{"array_id":"spectrum_000001:mz","array_type":"mz","byte_length":24,
 "checksum":"...64 lowercase hex...","data_offset":0,"dtype":"float64",
 "encoding":"raw-le","value_count":3}
```

Unknown/missing entry fields reject. JSON booleans are not integers. The raw
directory bytes must equal canonical reserialization, preventing alternate
spellings or duplicate-key ambiguity from becoming accepted wire forms.

Normative JSON Schema (the canonical-byte rule is an additional wire rule):

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "type":"object","additionalProperties":false,"required":["entries"],
  "properties":{"entries":{"type":"array","items":{
    "type":"object","additionalProperties":false,
    "required":["array_id","array_type","dtype","encoding","value_count","data_offset","byte_length","checksum"],
    "properties":{
      "array_id":{"type":"string","minLength":1},
      "array_type":{"enum":["mz","intensity","time"]},
      "dtype":{"const":"float64"},"encoding":{"const":"raw-le"},
      "value_count":{"type":"integer","minimum":0},
      "data_offset":{"type":"integer","minimum":0},
      "byte_length":{"type":"integer","minimum":0},
      "checksum":{"type":"string","pattern":"^[0-9a-f]{64}$"}
    }
  }}}
}
```

## 11. Entry field semantics

- `array_id`: nonempty, NUL-free UTF-8 string; unique and byte-for-byte equal
  to references in core blocks. The initial implementation limit is 4096
  UTF-8 bytes.
- `array_type`: exactly `mz`, `intensity`, or `time`. Auxiliary arrays remain
  in `extensions`.
- `dtype`: exactly `float64`; float32 is not an initial v2 core dtype.
- `encoding`: exactly `raw-le`, meaning uncompressed IEEE-754 binary64 in
  little-endian order.
- `value_count`: nonnegative integer; zero is valid at the format layer.
- `data_offset`: byte offset relative to payload start, never the arrays block
  or file. This avoids a directory-length/absolute-offset feedback loop.
- `byte_length`: exactly `value_count * 8`.
- `checksum`: lowercase SHA-256 of this array's payload bytes only.

## 12. Ordering and continuity

Entries are strictly ascending by `array_id.encode("utf-8")`. Payloads use the
same order. The first `data_offset` is zero. Every later offset equals the
previous `data_offset + byte_length`; gaps and overlaps reject. Duplicate IDs
reject before ordering is considered. `payload_length` equals the final end
and the sum of all byte lengths. This yields deterministic output independent
of Python insertion order and permits binary search or an instance-local map.

## 13. Alignment and padding

`payload_offset = align8(directory_offset + directory_length)`, where
`align8(n) = (n + 7) & ~7`. Every intervening byte is `0x00`; nonzero padding
rejects. Float64 payloads and every array boundary are therefore naturally
8-byte aligned. Padding belongs to the top-level arrays checksum but to no
per-array checksum.

## 14. Payload encoding

Payload is the direct concatenation of every entry's values as IEEE-754
binary64 little-endian bytes. No element header, compression, zlib, Numpress,
delta, varint, dictionary, pickle, `.npy`, Arrow, or platform object layout is
used. An empty array contributes zero bytes; adjacent zero-length entries may
share the same offset without creating a gap or overlap.

## 15. Two checksum levels

The top-level arrays checksum verifies the complete region and preserves the
nine-block integrity model. A complete Validator verifies it before trusting
the block as complete.

Each array checksum supports one-array reads, localized damage reports, and
future per-array caches/recovery. A random Reader verifies only the selected
array and need not scan the full payload. Neither checksum replaces the other.

## 16. Numeric constraints

Every decoded value is finite. NaN and positive/negative infinity reject.
`mz` and `time` values are nonnegative. Finite negative `intensity` is valid.
RT/time remains seconds. Validators perform semantic checks after checksum and
binary decode; they never normalize or repair invalid values.

## 17. Empty arrays behavior

At the format layer, `entry_count=0`, directory `{"entries":[]}`,
`payload_length=0`, and exact aligned block end are valid. The Golden empty
block is 80 bytes: 64 Header + 14 directory + 2 zero padding. Domain validation
may still reject missing/nonempty Spectrum or Chromatogram relationships.

## 18. Complete byte example

The committed nonempty Golden block is 824 bytes:

```text
Header             [0,64)
directory          [64,752) length=688
padding             none (752 already aligned)
payload            [752,824) length=72
 time              data_offset=0  byte_length=24
 intensity         data_offset=24 byte_length=24
 mz                data_offset=48 byte_length=24
```

Its SHA-256 is
`fc08d7123bd5abcb811d6fdbe5fff06b2250cb7e92727f5275d16cdb70cf7a5c`.

## 19. Hexdump example

```text
00000000  5a 50 41 52 52 56 32 00 02 00 01 00 03 00 00 00
00000010  40 00 00 00 00 00 00 00 b0 02 00 00 00 00 00 00
00000020  f0 02 00 00 00 00 00 00 48 00 00 00 00 00 00 00
00000030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00000040  7b 22 65 6e 74 72 69 65 73 22 3a 5b 7b 22 61 72
```

The first payload byte is at `0x2f0`; offsets in directory entries are
relative to that position.

## 20. Complete validation algorithm

1. Validate top-level Header/version, directory tail, nine entries, ranges,
   encodings, and all top-level checksums.
2. Before allocation, enforce arrays block and internal-directory limits.
3. Unpack exactly 64 bytes; validate magic/version/endianness/flags/reserved.
4. Checked-add directory and payload ranges; derive alignment and verify zero
   padding and exact block end.
5. Decode strict UTF-8 JSON; require canonical bytes and exact field sets.
6. Enforce count/ID/type/dtype/encoding/checksum/value-count limits.
7. Check UTF-8 ordering, uniqueness, `*8`, continuity, bounds, and total.
8. Verify every per-array checksum, unpack little-endian doubles, and apply
   numeric constraints.
9. Check every Spectrum/Chromatogram reference, type, and paired length.

No declared count or length is used to preallocate before its bounded range
and configured limit have passed.

## 21. Random read algorithm

Read and cache, per Reader instance, the top-level directory and internal
array directory. Locate `array_id`, seek to
`arrays_block_offset + payload_offset + data_offset`, read exactly
`byte_length`, verify that entry's checksum, unpack `<value_count>d`, and apply
that array's numeric constraints. Do not read the rest of payload, parse JSON
numbers, rebuild a `BlockCollection`, or treat this as a full validation.

## 22. Initial implementation safety limits

The format fields remain uint32/uint64; these are implementation defaults,
not reduced wire maxima:

| Resource | Default | Evidence/rationale |
|---|---:|---|
| arrays block | 512 MiB | about 6.9x measured 74.6 MB v1 arrays |
| internal directory | 64 MiB | far above the measured 4,098-entry need, bounded before read |
| entry_count | 100,000 | about 24x measured 4,098 arrays |
| one value_count | 16,000,000 | 128 MiB raw one-array ceiling |
| array_id UTF-8 | 4,096 bytes | generous for stable IDs; prevents hostile keys |
| payload | 448 MiB | leaves bounded Header/directory space inside 512 MiB |
| complete decoded memory | 1 GiB | explicit ceiling for reference/deep decode; random read is per-array |

Production limits must be configurable and checked before reads/allocation.
They deliberately permit a substantial v2 evaluation range above the 4.76M
values measured in P1-B6 without accepting arbitrary uint64 claims. Stable
codes are `ARRAYS_RESOURCE_LIMIT_EXCEEDED`, `ARRAY_ID_TOO_LONG`,
`ARRAY_DIRECTORY_TOO_LARGE`, `ARRAY_COUNT_TOO_LARGE`, and
`ARRAY_VALUE_COUNT_TOO_LARGE`.

## 23. Error codes

Frozen reference meanings are:

```text
INVALID_ARRAYS_MAGIC, UNSUPPORTED_ARRAYS_VERSION,
UNSUPPORTED_ARRAYS_ENDIANNESS, UNSUPPORTED_ARRAYS_FLAGS,
NONZERO_ARRAYS_RESERVED, INVALID_ARRAY_DIRECTORY_OFFSET,
INVALID_ARRAY_DIRECTORY_LENGTH, INVALID_ARRAY_PAYLOAD_OFFSET,
INVALID_ARRAY_PAYLOAD_LENGTH, ARRAY_PAYLOAD_MISALIGNED,
NONZERO_ARRAY_PADDING, ARRAYS_TRAILING_DATA,
ARRAY_ENTRY_COUNT_MISMATCH, DUPLICATE_ARRAY_ID,
UNSORTED_ARRAY_DIRECTORY, UNSUPPORTED_ARRAY_TYPE,
UNSUPPORTED_ARRAY_DTYPE, UNSUPPORTED_ARRAY_ENCODING,
INVALID_ARRAY_CHECKSUM_FORMAT, ARRAY_CHECKSUM_MISMATCH,
ARRAY_PAYLOAD_GAP, OVERLAPPING_ARRAY_PAYLOAD,
ARRAY_PAYLOAD_OUT_OF_BOUNDS, ARRAY_BYTE_LENGTH_MISMATCH,
INVALID_ARRAY_DIRECTORY_SCHEMA, NONFINITE_ARRAY_VALUE,
NEGATIVE_MZ_VALUE, NEGATIVE_TIME_VALUE,
ARRAYS_RESOURCE_LIMIT_EXCEEDED, ARRAY_ID_TOO_LONG,
ARRAY_DIRECTORY_TOO_LARGE, ARRAY_COUNT_TOO_LARGE,
ARRAY_VALUE_COUNT_TOO_LARGE, UNKNOWN_ARRAY_ID
```

## 24. Cross-language requirements

C/C++ must read fields individually or use a packed 1-byte struct with
static size/offset assertions; never rely on default alignment:

```c
#pragma pack(push, 1)
struct ZpArraysV2Header {
  uint8_t magic[8]; uint16_t schema_version; uint8_t endianness;
  uint8_t flags; uint32_t entry_count; uint64_t directory_offset;
  uint64_t directory_length; uint64_t payload_offset;
  uint64_t payload_length; uint8_t reserved[16];
};
#pragma pack(pop)
static_assert(sizeof(struct ZpArraysV2Header) == 64, "wire size");
```

Map fields to `uint8_t/uint16_t/uint32_t/uint64_t`, byte-swap on big-endian
hosts, and copy doubles with `memcpy` to avoid unaligned aliasing.

Rust maps fields to `[u8; 8]`, `u16`, `u8`, `u8`, `u32`, four `u64`, and
`[u8; 16]`; use `u*_from_le_bytes`, `f64::from_bits`, checked additions, and
do not transmute an untrusted `repr(C)` buffer. The conceptual Rust record is:

```rust
struct ArraysHeader { magic: [u8;8], schema_version: u16, endianness: u8,
flags: u8, entry_count: u32, directory_offset: u64, directory_length: u64,
payload_offset: u64, payload_length: u64, reserved: [u8;16] }
```

Java uses `ByteBuffer.order(ByteOrder.LITTLE_ENDIAN)` and `getDouble`; signed
`long` values must be range-checked before allocation. C# uses
`BinaryPrimitives.ReadUInt64LittleEndian` and converts 64-bit bits with
`BitConverter.Int64BitsToDouble`; do not use platform-native endianness.

All languages decode the directory as strict UTF-8, implement the stated
canonical JSON check, use SHA-256 over exact bytes, treat `data_offset` as
payload-relative, enforce 8-byte alignment, and verify structural bounds
before checksums, then checksum before numeric semantics.

## 25. Future compression boundary

`raw-le` is the only version-2 initial encoding. A future compression proposal
must define seek granularity, decompressed length limits, checksum coverage,
bomb resistance, deterministic parameters, and version/subformat dispatch.
It may not silently assign zlib or another meaning to `raw-le`. Whether that
requires ZP v3 or a reviewed arrays schema version is a separate decision.

## 26. Explicitly unsupported

The initial v2 arrays format does not support float32, non-little-endian data,
nonzero flags, nonzero reserved bytes, compression, auxiliary core array
types, multiple arrays blocks, payload gaps/overlaps, noncanonical JSON,
nonfinite values, negative m/z/time, sidecars, Python serialization, or
version inference from encoding/magic. It does not change core Block fields or
claim production v2 availability.
