from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from binary_layer.mzml_admission import evaluate_mzml_admission
from mzml_test_support import build_feature_profile, inspect_xml, local_name

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
ENTRIES = MANIFEST["fixtures"]


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda item: item["fixture_name"])
def test_fixture_exists_is_small_and_is_well_formed_xml(entry: dict[str, object]) -> None:
    path = FIXTURE_DIR / str(entry["fixture_name"])
    assert path.is_file()
    assert path.stat().st_size < 100_000
    assert local_name(ET.parse(path).getroot().tag) in {"mzML", "indexedmzML"}


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda item: item["fixture_name"])
def test_fixture_structure_and_admission_match_manifest(entry: dict[str, object]) -> None:
    path = FIXTURE_DIR / str(entry["fixture_name"])
    profile = build_feature_profile(path)
    result = evaluate_mzml_admission(profile)
    assert profile.indexed is entry["indexed"]
    assert profile.run_count == entry["run_count"]
    assert len(profile.spectra) == entry["spectrum_count"]
    assert len(profile.chromatograms) == entry["chromatogram_count"]
    assert sorted({item.ms_level for item in profile.spectra}) == entry["ms_levels"]
    assert result.accepted is entry["expected_admission"]
    assert {item.code for item in result.issues} == set(entry["expected_issue_codes"])


def test_manifest_has_required_fields_and_accept_reject_coverage() -> None:
    required = {
        "fixture_name", "expected_admission", "expected_issue_codes", "indexed",
        "run_count", "spectrum_count", "chromatogram_count", "ms_levels",
        "representation", "rt_units", "array_dtypes", "array_compression", "notes",
        "expected_adapter_error",
    }
    assert MANIFEST["schema_version"] == 1
    assert all(set(item) == required for item in ENTRIES)
    assert sum(item["expected_admission"] is True for item in ENTRIES) == 10
    assert sum(item["expected_admission"] is False for item in ENTRIES) == 19


def test_indexed_fixture_offsets_and_checksum_are_real() -> None:
    path = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    raw = path.read_bytes()
    root = ET.parse(path).getroot()
    indexes = [item for item in root if local_name(item.tag) == "indexList"]
    assert len(indexes) == 1
    for index in indexes[0]:
        assert local_name(index.tag) == "index"
        expected_tag = b"<spectrum" if index.attrib["name"] == "spectrum" else b"<chromatogram"
        for offset in index:
            assert raw[int(offset.text or "-1"):].startswith(expected_tag)
    index_offset = next(item for item in root if local_name(item.tag) == "indexListOffset")
    assert raw[int(index_offset.text or "-1"):].startswith(b"<indexList")
    checksum = next(item for item in root if local_name(item.tag) == "fileChecksum")
    checksum_value_start = raw.index(b"<fileChecksum>") + len(b"<fileChecksum>")
    assert checksum.text == hashlib.sha1(raw[:checksum_value_start]).hexdigest()


def test_fixture_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    script = FIXTURE_DIR / "build_fixtures.py"
    subprocess.run([sys.executable, str(script), "--output-dir", str(tmp_path)], check=True)
    generated = sorted(path.name for path in tmp_path.iterdir())
    expected = sorted(["manifest.json", *(str(item["fixture_name"]) for item in ENTRIES)])
    assert generated == expected
    for name in expected:
        assert (tmp_path / name).read_bytes() == (FIXTURE_DIR / name).read_bytes()
    assert not list(tmp_path.glob("*.zp"))


def test_fixture_builder_has_no_production_conversion_imports() -> None:
    script = FIXTURE_DIR / "build_fixtures.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "binary_layer" or name.startswith("binary_layer.") for name in imported)
    assert not list(FIXTURE_DIR.glob("*.zp"))


def test_fixture_directory_contains_no_large_real_sample() -> None:
    mzml_files = list(FIXTURE_DIR.glob("*.mzML"))
    assert len(mzml_files) == 29
    assert sum(path.stat().st_size for path in mzml_files) < 400_000
    assert max(path.stat().st_size for path in mzml_files) < 100_000


def test_manifest_encoding_claims_match_xml_metadata() -> None:
    for entry in ENTRIES:
        xml = inspect_xml(FIXTURE_DIR / str(entry["fixture_name"]))
        arrays = [array for owner in (*xml.spectra.values(), *xml.chromatograms.values()) for array in owner.arrays]
        assert set(entry["array_dtypes"]) == {array.dtype for array in arrays if array.dtype}
        assert set(entry["array_compression"]) == {array.compression for array in arrays if array.compression}
