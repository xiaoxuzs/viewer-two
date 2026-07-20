from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from binary_layer.blocks import (
    ISOLATION_WINDOW_KIND,
    SELECTED_PRECURSOR_KIND,
    PrecursorBlock,
)
from binary_layer.logical_fingerprint import build_logical_fingerprint
from binary_layer.migration import migrate_v1_to_v2
from binary_layer.precursor_contract import validate_precursor_record
from binary_layer.exceptions import ZpWriteError
from binary_layer.reader import ZpReader
from binary_layer.serialization import canonical_json_bytes
from binary_layer.validator import ZpValidator
from binary_layer.writer import ZpWriter
from specs.zp_full.logical_model import LogicalZpDocument
from zp_compatibility_support import (
    build_full_blocks,
    mutate_pair,
    validation_summary,
    write_pair,
    write_zp,
)


def _precursor_blocks(kind: str):
    blocks = build_full_blocks()
    assert blocks.global_meta is not None
    blocks.global_meta = replace(
        blocks.global_meta,
        chromatogram_count=0,
        array_count=4,
    )
    blocks.runs = [replace(blocks.runs[0], chromatogram_count=0)]
    blocks.chromatograms = []
    blocks.arrays = [item for item in blocks.arrays if item.array_id.startswith("spectrum_")]
    blocks.extensions = []
    original = blocks.precursors[0]
    if kind == "legacy":
        return blocks
    if kind == SELECTED_PRECURSOR_KIND:
        blocks.precursors = [replace(original, precursor_kind=SELECTED_PRECURSOR_KIND)]
        return blocks
    if kind == ISOLATION_WINDOW_KIND:
        blocks.precursors = [
            PrecursorBlock(
                precursor_id=original.precursor_id,
                spectrum_id=original.spectrum_id,
                precursor_mz=None,
                charge=None,
                intensity=None,
                precursor_kind=ISOLATION_WINDOW_KIND,
                isolation_lower_mz=375.5,
                isolation_upper_mz=388.5,
            )
        ]
        return blocks
    raise AssertionError(kind)


def _precursor(blocks: dict[str, object]) -> dict[str, object]:
    records = blocks["core_precursors"]
    assert isinstance(records, list) and isinstance(records[0], dict)
    return records[0]


def test_model_exposes_effective_kind_without_changing_legacy_json() -> None:
    legacy = PrecursorBlock("p1", "s2", 445.2, 2, 50.0)
    explicit = replace(legacy, precursor_kind=SELECTED_PRECURSOR_KIND)
    window = PrecursorBlock(
        "p2",
        "s3",
        None,
        None,
        None,
        ISOLATION_WINDOW_KIND,
        375.5,
        388.5,
    )

    assert legacy.precursor_kind is None
    assert legacy.effective_precursor_kind == SELECTED_PRECURSOR_KIND
    assert explicit.effective_precursor_kind == SELECTED_PRECURSOR_KIND
    assert window.effective_precursor_kind == ISOLATION_WINDOW_KIND
    assert canonical_json_bytes([legacy]) == (
        b'[{"charge":2,"intensity":50.0,"precursor_id":"p1",'
        b'"precursor_mz":445.2,"spectrum_id":"s2"}]'
    )


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("kind", ["legacy", SELECTED_PRECURSOR_KIND, ISOLATION_WINDOW_KIND])
def test_precursor_contract_writer_reader_validator_roundtrip(
    version: int,
    kind: str,
    tmp_path: Path,
) -> None:
    path = write_zp(tmp_path / f"{kind}-v{version}.zp", _precursor_blocks(kind), version)

    validation = ZpValidator().validate(path)
    raw = ZpReader(path).read_block("core_precursors")[0]
    precursor = ZpReader(path).read_precursors()[0]

    assert validation.valid is True
    assert validation.checked_blocks == 9
    assert validation.issues == []
    assert precursor.effective_precursor_kind == (
        ISOLATION_WINDOW_KIND if kind == ISOLATION_WINDOW_KIND else SELECTED_PRECURSOR_KIND
    )
    if kind == "legacy":
        assert "precursor_kind" not in raw
        assert "isolation_lower_mz" not in raw
        assert "isolation_upper_mz" not in raw
    elif kind == SELECTED_PRECURSOR_KIND:
        assert raw["precursor_kind"] == SELECTED_PRECURSOR_KIND
        assert precursor.charge == 2
    else:
        assert raw["precursor_kind"] == ISOLATION_WINDOW_KIND
        assert raw["charge"] is None
        assert precursor.precursor_mz is None
        assert precursor.intensity is None
        assert precursor.isolation_lower_mz == 375.5
        assert precursor.isolation_upper_mz == 388.5


def _set_unknown_kind(blocks: dict[str, object]) -> None:
    _precursor(blocks)["precursor_kind"] = "unknown"


def _set_selected_window_field(blocks: dict[str, object]) -> None:
    _precursor(blocks)["isolation_lower_mz"] = 375.5


def _remove_charge(blocks: dict[str, object]) -> None:
    _precursor(blocks).pop("charge")


def _set_charge(value: object):
    def mutate(blocks: dict[str, object]) -> None:
        _precursor(blocks)["charge"] = value

    return mutate


def _remove_window(field: str):
    def mutate(blocks: dict[str, object]) -> None:
        _precursor(blocks).pop(field)

    return mutate


def _set_window(lower: float, upper: float):
    def mutate(blocks: dict[str, object]) -> None:
        record = _precursor(blocks)
        record["isolation_lower_mz"] = lower
        record["isolation_upper_mz"] = upper

    return mutate


@pytest.mark.parametrize(
    ("base_kind", "mutation", "expected_code"),
    [
        ("legacy", _set_unknown_kind, "INVALID_PRECURSOR_KIND"),
        ("legacy", _remove_charge, "MISSING_PRECURSOR_CHARGE"),
        ("legacy", _set_charge(None), "MISSING_PRECURSOR_CHARGE"),
        ("legacy", _set_charge(0), "INVALID_PRECURSOR_CHARGE"),
        ("legacy", _set_charge(-1), "INVALID_PRECURSOR_CHARGE"),
        ("legacy", _set_selected_window_field, "PRECURSOR_KIND_FIELD_CONFLICT"),
        (ISOLATION_WINDOW_KIND, _set_charge(2), "PRECURSOR_KIND_FIELD_CONFLICT"),
        (ISOLATION_WINDOW_KIND, _remove_window("isolation_lower_mz"), "MISSING_ISOLATION_WINDOW"),
        (ISOLATION_WINDOW_KIND, _remove_window("isolation_upper_mz"), "MISSING_ISOLATION_WINDOW"),
        (ISOLATION_WINDOW_KIND, _set_window(388.5, 388.5), "INVALID_ISOLATION_WINDOW"),
        (ISOLATION_WINDOW_KIND, _set_window(400.0, 388.5), "INVALID_ISOLATION_WINDOW"),
    ],
)
def test_invalid_precursor_combinations_are_rejected_identically_in_v1_and_v2(
    base_kind: str,
    mutation,
    expected_code: str,
    tmp_path: Path,
) -> None:
    paths = write_pair(tmp_path, _precursor_blocks(base_kind))
    mutate_pair(paths, mutation)
    results = {version: validation_summary(path) for version, path in paths.items()}

    assert results[1]["valid"] is results[2]["valid"] is False
    assert expected_code in results[1]["codes"]
    assert results[1]["codes"] == results[2]["codes"]
    assert results[1]["checked_blocks"] == results[2]["checked_blocks"] == 9


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["isolation_lower_mz", "isolation_upper_mz"])
def test_nonfinite_isolation_window_values_are_rejected(field: str, value: float) -> None:
    record = {
        "precursor_id": "p1",
        "spectrum_id": "s2",
        "precursor_mz": None,
        "charge": None,
        "intensity": None,
        "precursor_kind": ISOLATION_WINDOW_KIND,
        "isolation_lower_mz": 375.5,
        "isolation_upper_mz": 388.5,
    }
    record[field] = value

    assert [item.code for item in validate_precursor_record(record)] == [
        "INVALID_ISOLATION_WINDOW"
    ]


def _fingerprint_blocks(precursor: dict[str, object]) -> dict[str, object]:
    return {
        "global_meta": {"format_version": 1},
        "string_pool": {"strings": []},
        "core_runs": [],
        "core_spectra": [],
        "core_precursors": [precursor],
        "core_chromatograms": [],
        "indexes": {"scan_index": [], "rt_index": [], "spectrum_id_index": []},
        "extensions": [],
    }


def test_logical_fingerprint_normalizes_legacy_selected_kind_but_keeps_window_semantics() -> None:
    legacy = {
        "precursor_id": "p1",
        "spectrum_id": "s2",
        "precursor_mz": 445.2,
        "charge": 2,
        "intensity": 50.0,
    }
    explicit = {**legacy, "precursor_kind": SELECTED_PRECURSOR_KIND}
    window = {
        **legacy,
        "precursor_kind": ISOLATION_WINDOW_KIND,
        "precursor_mz": None,
        "charge": None,
        "intensity": None,
        "isolation_lower_mz": 375.5,
        "isolation_upper_mz": 388.5,
    }

    legacy_hash = build_logical_fingerprint(_fingerprint_blocks(legacy), []).sha256
    explicit_hash = build_logical_fingerprint(_fingerprint_blocks(explicit), []).sha256
    window_hash = build_logical_fingerprint(_fingerprint_blocks(window), []).sha256

    assert legacy_hash == explicit_hash
    assert window_hash != legacy_hash


def test_independent_logical_model_normalizes_legacy_selected_kind() -> None:
    legacy = _fingerprint_blocks(
        {
            "precursor_id": "p1",
            "spectrum_id": "s2",
            "precursor_mz": 445.2,
            "charge": 2,
            "intensity": 50.0,
        }
    )
    explicit = deepcopy(legacy)
    _precursor(explicit)["precursor_kind"] = SELECTED_PRECURSOR_KIND

    def report(blocks: dict[str, object]) -> dict[str, object]:
        return {"blocks": {**blocks, "arrays": []}, "arrays": []}

    assert LogicalZpDocument.from_inspection(report(legacy)).precursors == (
        LogicalZpDocument.from_inspection(report(explicit)).precursors
    )


@pytest.mark.parametrize("kind", ["legacy", ISOLATION_WINDOW_KIND])
def test_v1_to_v2_migration_preserves_precursor_contract(kind: str, tmp_path: Path) -> None:
    source = write_zp(tmp_path / f"{kind}-v1.zp", _precursor_blocks(kind), 1)
    target = tmp_path / f"{kind}-v2.zp"

    result = migrate_v1_to_v2(source, target)
    validation = ZpValidator().validate(target)
    source_precursor = ZpReader(source).read_precursors()[0]
    target_precursor = ZpReader(target).read_precursors()[0]

    assert result.source_logical_fingerprint == result.target_logical_fingerprint
    assert validation.valid is True
    assert validation.checked_blocks == 9
    assert target_precursor == source_precursor
    assert target_precursor.effective_precursor_kind == (
        ISOLATION_WINDOW_KIND if kind == ISOLATION_WINDOW_KIND else SELECTED_PRECURSOR_KIND
    )
    if kind == ISOLATION_WINDOW_KIND:
        assert target_precursor.charge is None
        assert target_precursor.isolation_lower_mz == 375.5
        assert target_precursor.isolation_upper_mz == 388.5


def test_writer_rejects_nonfinite_window_before_creating_a_file(tmp_path: Path) -> None:
    blocks = _precursor_blocks(ISOLATION_WINDOW_KIND)
    blocks.precursors[0] = replace(blocks.precursors[0], isolation_lower_mz=math.nan)
    target = tmp_path / "nonfinite.zp"

    with pytest.raises(ZpWriteError, match="not JSON compliant"):
        ZpWriter().write(target, blocks)

    assert target.exists() is False
