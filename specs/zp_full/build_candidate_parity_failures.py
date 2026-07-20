"""Build the three preserved parity probes through P1-B8.5R3B."""

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
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
    ZpValidator,
    ZpWriter,
)
from binary_layer.constants import DIRECTORY_LENGTH_STRUCT, HEADER_STRUCT  # noqa: E402
from binary_layer.serialization import canonical_json_bytes  # noqa: E402


FIXED_CREATED_AT = datetime(2026, 7, 15, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).with_name("failures") / "candidate_parity"
MANIFEST_NAME = "manifest.json"


def build_blocks() -> BlockCollection:
    source = "candidate-parity.mzML"
    native_ids = (
        "controllerType=0 controllerNumber=1 scan=1",
        "controllerType=0 controllerNumber=1 scan=2",
    )
    arrays = [
        ArrayBlock("spectrum_000001:mz", "mz", "float64", [100.0]),
        ArrayBlock("spectrum_000001:intensity", "intensity", "float64", [10.0]),
        ArrayBlock("spectrum_000002:mz", "mz", "float64", [200.0]),
        ArrayBlock("spectrum_000002:intensity", "intensity", "float64", [20.0]),
    ]
    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name=source,
            source_file_hash="0" * 64,
            run_count=1,
            spectrum_count=2,
            chromatogram_count=0,
            array_count=4,
            created_at=FIXED_CREATED_AT,
            generator_name="zp-full-compatibility-audit",
            generator_version="1",
            notes=["P1-B8.5R2 candidate parity evidence."],
        ),
        runs=[RunBlock("run_000001", source, "run", 2, 0, 0.125, 0.25)],
        spectra=[
            SpectrumBlock(
                "spectrum_000001",
                "run_000001",
                1,
                1,
                native_ids[0],
                0.125,
                None,
                "spectrum_000001:mz",
                "spectrum_000001:intensity",
            ),
            SpectrumBlock(
                "spectrum_000002",
                "run_000001",
                2,
                2,
                native_ids[1],
                0.25,
                "precursor_000001",
                "spectrum_000002:mz",
                "spectrum_000002:intensity",
            ),
        ],
        precursors=[PrecursorBlock("precursor_000001", "spectrum_000002", 150.0, 2, 50.0)],
        arrays=arrays,
        string_pool=StringPoolBlock([source, "run", *native_ids]),
        indexes=IndexBlock(
            scan_index=[
                {"scan_number": 1, "spectrum_id": "spectrum_000001"},
                {"scan_number": 2, "spectrum_id": "spectrum_000002"},
            ],
            rt_index=[
                {"rt": 0.125, "spectrum_id": "spectrum_000001"},
                {"rt": 0.25, "spectrum_id": "spectrum_000002"},
            ],
            spectrum_id_index=[
                {"position": 0, "spectrum_id": "spectrum_000001"},
                {"position": 1, "spectrum_id": "spectrum_000002"},
            ],
        ),
    )


def _mutate_json_block(path: Path, block_name: str, mutate) -> None:
    data = bytearray(path.read_bytes())
    _magic, _version, _endianness, _flags, _created_at, directory_offset = HEADER_STRUCT.unpack_from(data)
    directory_length = DIRECTORY_LENGTH_STRUCT.unpack_from(data, directory_offset)[0]
    directory_start = directory_offset + DIRECTORY_LENGTH_STRUCT.size
    directory_end = directory_start + directory_length
    directory = json.loads(bytes(data[directory_start:directory_end]).decode("utf-8"))
    entry = next(item for item in directory if item["block_name"] == block_name)
    block_start = entry["offset"]
    block_end = block_start + entry["length"]
    value = json.loads(bytes(data[block_start:block_end]).decode("utf-8"))
    mutate(value)
    payload = canonical_json_bytes(value)
    if len(payload) != entry["length"]:
        raise RuntimeError("length-preserving probe mutation changed block length")
    data[block_start:block_end] = payload
    entry["checksum"] = hashlib.sha256(payload).hexdigest()
    encoded_directory = canonical_json_bytes(directory)
    if len(encoded_directory) != directory_length:
        raise RuntimeError("probe checksum update changed directory length")
    data[directory_start:directory_end] = encoded_directory
    path.write_bytes(data)


PROBES = (
    (
        "run_count",
        "core_runs",
        lambda value: value[0].update(spectrum_count=1),
        "Run spectrum_count is 1 while two Spectra belong to the Run",
    ),
    (
        "string_pool",
        "string_pool",
        lambda value: value["strings"].__setitem__(1, "xxx"),
        "string_pool omits the referenced Run name 'run'",
    ),
    (
        "precursor_bidirectional",
        "core_precursors",
        lambda value: value[0].update(spectrum_id="spectrum_000001"),
        "Precursor points to an existing MS1 instead of its owning MS2",
    ),
)


def _build_into(directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    probes: list[dict[str, object]] = []
    for probe_name, block_name, mutate, corruption in PROBES:
        records: list[dict[str, object]] = []
        for version in (1, 2):
            name = f"{probe_name}_mismatch_v{version}.zp"
            path = directory / name
            with patch("binary_layer.writer.time.time", return_value=FIXED_CREATED_AT.timestamp()):
                ZpWriter().write(path, build_blocks(), format_version=version)
            baseline = ZpValidator().validate(path)
            if not baseline.valid or baseline.issues or baseline.checked_blocks != 9:
                raise RuntimeError(f"version {version} source document is not valid")
            _mutate_json_block(path, block_name, mutate)
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
        probes.append({"probe": probe_name, "logical_corruption": corruption, "fixtures": records})
    manifest = {
        "purpose": "P1-B8.5R3B preserved candidate parity evidence",
        "probes": probes,
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
        expected_names = {MANIFEST_NAME} | {
            f"{probe_name}_mismatch_v{version}.zp"
            for probe_name, _block_name, _mutate, _corruption in PROBES
            for version in (1, 2)
        }
        actual_names = {
            item.name
            for item in FIXTURE_DIR.iterdir()
            if item.is_file() and item.name != "README.md"
        }
        if actual_names != expected_names:
            raise SystemExit(f"candidate failure Fixture file set drifted: {sorted(actual_names)}")
        for name in sorted(expected_names):
            if (generated / name).read_bytes() != (FIXTURE_DIR / name).read_bytes():
                raise SystemExit(f"candidate failure Fixture drifted: {name}")
    print(json.dumps({"candidate_parity_failure_fixtures_deterministic": True}, sort_keys=True))


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
