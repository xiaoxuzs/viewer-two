"""Build the minimal P1-B8.5 GlobalMeta count-parity failure evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer import (  # noqa: E402
    ArrayBlock,
    BlockCollection,
    GlobalMetaBlock,
    IndexBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
    ZpValidator,
    ZpWriter,
)
from binary_layer.constants import DIRECTORY_LENGTH_STRUCT, HEADER_STRUCT  # noqa: E402
from binary_layer.serialization import canonical_json_bytes  # noqa: E402


FIXED_CREATED_AT = datetime(2026, 7, 15, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).with_name("failures") / "global_meta_count"
MANIFEST_NAME = "manifest.json"


def build_blocks() -> BlockCollection:
    """Return the valid logical document used before the one-field mutation."""

    source = "global-meta-count-failure.mzML"
    native_id = "controllerType=0 controllerNumber=1 scan=1"
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name=source,
            source_file_hash="0" * 64,
            run_count=1,
            spectrum_count=1,
            chromatogram_count=0,
            array_count=2,
            created_at=FIXED_CREATED_AT,
            generator_name="zp-full-compatibility-audit",
            generator_version="1",
            notes=["P1-B8.5 minimal GlobalMeta count-parity evidence."],
        ),
        runs=[
            RunBlock(
                run_id="run_000001",
                source_file=source,
                run_name="global-meta-count-failure",
                spectrum_count=1,
                chromatogram_count=0,
                start_rt=0.125,
                end_rt=0.125,
            )
        ],
        spectra=[
            SpectrumBlock(
                spectrum_id="spectrum_000001",
                run_id="run_000001",
                ms_level=1,
                scan_number=1,
                native_id=native_id,
                rt=0.125,
                precursor_id=None,
                mz_array_id="spectrum_000001:mz",
                intensity_array_id="spectrum_000001:intensity",
            )
        ],
        arrays=[
            ArrayBlock("spectrum_000001:mz", "mz", "float64", [100.125, 250.5]),
            ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [0.0, -2.5]),
        ],
        string_pool=StringPoolBlock([source, "global-meta-count-failure", native_id]),
        indexes=IndexBlock(
            scan_index=[{"scan_number": 1, "spectrum_id": "spectrum_000001"}],
            rt_index=[{"rt": 0.125, "spectrum_id": "spectrum_000001"}],
            spectrum_id_index=[{"position": 0, "spectrum_id": "spectrum_000001"}],
        ),
    )


def _set_global_run_count_to_zero(path: Path) -> None:
    data = bytearray(path.read_bytes())
    _magic, _version, _endianness, _flags, _created_at, directory_offset = HEADER_STRUCT.unpack_from(data)
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack_from(data, directory_offset)[0]
    directory_start = directory_offset + DIRECTORY_LENGTH_STRUCT.size
    directory_end = directory_start + directory_length
    directory = json.loads(bytes(data[directory_start:directory_end]).decode("utf-8"))
    entry = next(item for item in directory if item["block_name"] == "global_meta")
    block_start = entry["offset"]
    block_end = block_start + entry["length"]
    global_meta = json.loads(bytes(data[block_start:block_end]).decode("utf-8"))
    global_meta["run_count"] = 0
    payload = canonical_json_bytes(global_meta)
    if len(payload) != entry["length"]:
        raise RuntimeError("one-digit count mutation unexpectedly changed block length")
    data[block_start:block_end] = payload
    entry["checksum"] = hashlib.sha256(payload).hexdigest()
    encoded_directory = canonical_json_bytes(directory)
    if len(encoded_directory) != directory_length:
        raise RuntimeError("checksum update unexpectedly changed directory length")
    data[directory_start:directory_end] = encoded_directory
    path.write_bytes(data)


def _build_into(directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for version in (1, 2):
        name = f"global_meta_count_mismatch_v{version}.zp"
        path = directory / name
        with patch("binary_layer.writer.time.time", return_value=FIXED_CREATED_AT.timestamp()):
            ZpWriter().write(path, build_blocks(), format_version=version)
        baseline = ZpValidator().validate(path)
        if not baseline.valid or baseline.issues or baseline.checked_blocks != 9:
            raise RuntimeError(f"version {version} source document is not valid")
        _set_global_run_count_to_zero(path)
        result = ZpValidator().validate(path)
        records.append(
            {
                "file": name,
                "format_version": version,
                "file_size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "validator_valid": result.valid,
                "validator_checked_blocks": result.checked_blocks,
                "validator_issue_codes": [issue.code for issue in result.issues],
            }
        )
    manifest = {
        "purpose": "P1-B8.5 minimal cross-version GlobalMeta count semantic-drift evidence",
        "logical_corruption": "global_meta.run_count is 0 while one Run exists",
        "fixtures": records,
    }
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _check() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary)
        _build_into(generated)
        expected_names = {
            "global_meta_count_mismatch_v1.zp",
            "global_meta_count_mismatch_v2.zp",
            MANIFEST_NAME,
        }
        actual_names = {
            item.name
            for item in FIXTURE_DIR.iterdir()
            if item.is_file() and item.name != "README.md"
        }
        if actual_names != expected_names:
            raise SystemExit(f"failure Fixture file set drifted: {sorted(actual_names)}")
        for name in sorted(expected_names):
            if (generated / name).read_bytes() != (FIXTURE_DIR / name).read_bytes():
                raise SystemExit(f"failure Fixture drifted: {name}")
    print(json.dumps({"global_meta_count_failure_fixture_deterministic": True}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        _check()
        return
    print(json.dumps(_build_into(FIXTURE_DIR), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
