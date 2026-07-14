import hashlib
import json
import struct
from pathlib import Path


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_v2" / "fixtures"


def test_manifest_and_golden_bytes_are_independently_verified() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["format"], manifest["schema_version"]) == ("zp-arrays-v2", 2)
    for record in manifest["fixtures"]:
        raw = (FIXTURE_DIR / record["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        assert len(raw) == record["block_size"]
        header = struct.Struct("<8sHBBIQQQQ16s").unpack(raw[:64])
        _, _, _, _, entry_count, directory_offset, directory_length, payload_offset, payload_length, _ = header
        assert entry_count == record["entry_count"]
        assert directory_length == record["directory_length"]
        assert payload_offset == record["payload_offset"] == (64 + directory_length + 7) & ~7
        assert payload_length == record["payload_length"]
        directory_raw = raw[directory_offset:directory_offset + directory_length]
        directory = json.loads(directory_raw.decode("utf-8"))
        assert directory_raw == json.dumps(directory, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        assert len(directory["entries"]) == entry_count
        for entry, expected in zip(directory["entries"], record["arrays"]):
            assert entry == {key: value for key, value in expected.items() if key != "values"}
            start = payload_offset + entry["data_offset"]
            payload = raw[start:start + entry["byte_length"]]
            assert len(payload) == entry["byte_length"]
            assert hashlib.sha256(payload).hexdigest() == entry["checksum"]
            assert list(struct.unpack(f"<{entry['value_count']}d", payload)) == expected["values"]
        assert payload_offset + payload_length == len(raw)


def test_committed_fixture_hashes_are_frozen() -> None:
    expected = {
        "valid_arrays_v2.bin": "fc08d7123bd5abcb811d6fdbe5fff06b2250cb7e92727f5275d16cdb70cf7a5c",
        "valid_empty_arrays_v2.bin": "a81b75aaa9e6f59ea15b9b3fe9bb4cb386e0ca30db253d196c852151a8d46616",
    }
    assert {name: hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest() for name in expected} == expected

