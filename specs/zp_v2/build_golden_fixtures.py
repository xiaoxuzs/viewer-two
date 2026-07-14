from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from .arrays_reference_codec import ReferenceArray, decode_arrays_block, encode_arrays_block
except ImportError:
    from arrays_reference_codec import ReferenceArray, decode_arrays_block, encode_arrays_block


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURES = {
    "valid_arrays_v2.bin": (
        ReferenceArray("chromatogram_000001:time", "time", (0.0, 0.125, 12.75)),
        ReferenceArray("spectrum_000001:intensity", "intensity", (0.0, -2.5, 1500.25)),
        ReferenceArray("spectrum_000001:mz", "mz", (0.0, 100.125, 2500.75)),
    ),
    "valid_empty_arrays_v2.bin": (),
}


def _record(name: str, data: bytes) -> dict[str, object]:
    decoded = decode_arrays_block(data)
    entries = decoded.directory["entries"]
    arrays = []
    values_by_id = {item.array_id: list(item.values) for item in decoded.arrays}
    for entry in entries:
        arrays.append({**entry, "values": values_by_id[entry["array_id"]]})
    return {
        "file": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "block_size": len(data),
        "entry_count": len(entries),
        "directory_length": decoded.directory_length,
        "payload_offset": decoded.payload_offset,
        "payload_length": decoded.payload_length,
        "arrays": arrays,
    }


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for name, arrays in FIXTURES.items():
        data = encode_arrays_block(arrays)
        (FIXTURE_DIR / name).write_bytes(data)
        records.append(_record(name, data))
    manifest = {"format": "zp-arrays-v2", "schema_version": 2, "fixtures": records}
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    (FIXTURE_DIR / "manifest.json").write_bytes(manifest_bytes)
    for record in records:
        print(f"{record['file']} {record['block_size']} {record['sha256']}")


if __name__ == "__main__":
    main()
