"""Build the minimal P1-B8.5 Validator semantic-drift evidence.

This is intentionally not a Golden release fixture.  It records the first
cross-version domain-rule mismatch found by the B8.5 audit and must be removed
or replaced only in a separate production-correction stage.
"""

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
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
    ZpValidator,
    ZpWriter,
)


FIXED_CREATED_AT = datetime(2026, 7, 14, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).with_name("failures")
MANIFEST_NAME = "manifest.json"


def build_blocks() -> BlockCollection:
    """Return one valid minimal document except for one Extension owner."""

    return BlockCollection(
        global_meta=GlobalMetaBlock(
            format_version=1,
            source_type="fixture",
            source_file_name="extension-owner-failure.mzML",
            source_file_hash="0" * 64,
            run_count=1,
            spectrum_count=1,
            chromatogram_count=0,
            array_count=2,
            created_at=FIXED_CREATED_AT,
            generator_name="zp-full-compatibility-audit",
            generator_version="1",
            notes=["P1-B8.5 minimal Extension owner mismatch evidence."],
        ),
        runs=[
            RunBlock(
                run_id="run_000001",
                source_file="extension-owner-failure.mzML",
                run_name="extension-owner-failure",
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
                native_id="controllerType=0 controllerNumber=1 scan=1",
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
        string_pool=StringPoolBlock(
            [
                "extension-owner-failure.mzML",
                "extension-owner-failure",
                "controllerType=0 controllerNumber=1 scan=1",
            ]
        ),
        indexes=IndexBlock(
            scan_index=[{"scan_number": 1, "spectrum_id": "spectrum_000001"}],
            rt_index=[{"rt": 0.125, "spectrum_id": "spectrum_000001"}],
            spectrum_id_index=[{"position": 0, "spectrum_id": "spectrum_000001"}],
        ),
        extensions=[
            ExtensionBlock(
                extension_type="mzml_auxiliary_arrays",
                extension_version="1",
                payload={
                    "arrays": [
                        {
                            "owner_kind": "chromatogram",
                            "owner_id": "missing_chromatogram",
                            "array_accession": "MS:1000786",
                            "array_name": "ms level",
                            "dtype": "int64",
                            "values": [1],
                            "unit_accession": "UO:0000186",
                            "unit_name": "dimensionless unit",
                        }
                    ],
                    "schema_version": 1,
                },
            )
        ],
    )


def _build_into(directory: Path) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    fixed_epoch_seconds = FIXED_CREATED_AT.timestamp()
    for version in (1, 2):
        name = f"extension_owner_mismatch_v{version}.zp"
        path = directory / name
        with patch("binary_layer.writer.time.time", return_value=fixed_epoch_seconds):
            ZpWriter().write(path, build_blocks(), format_version=version)
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
        "purpose": "P1-B8.5 minimal cross-version Extension owner semantic-drift evidence",
        "logical_corruption": "mzml_auxiliary_arrays owner_id references a missing chromatogram",
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
            "extension_owner_mismatch_v1.zp",
            "extension_owner_mismatch_v2.zp",
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
    print(json.dumps({"failure_fixture_deterministic": True}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        _check()
        return
    manifest = _build_into(FIXTURE_DIR)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
