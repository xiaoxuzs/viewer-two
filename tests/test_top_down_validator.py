from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import TopDownReader, convert_source_to_zp, validate_zp
from binary_layer.conversion_exceptions import TopDownSchemaError
from conftest import rewrite_zp
from top_down_support import build_top_down_bundle


def _extension(payloads: dict[str, object], extension_type: str) -> dict[str, object]:
    extensions = payloads["extensions"]
    assert isinstance(extensions, list)
    return next(
        item for item in extensions
        if isinstance(item, dict) and item.get("extension_type") == extension_type
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payloads: _extension(payloads, "top_down_prsms")["payload"]["records"][0].__setitem__(
                "spectrum_id", "missing-spectrum"
            ),
            "TOP_DOWN_SPECTRUM_REFERENCE_NOT_FOUND",
        ),
        (
            lambda payloads: _extension(payloads, "top_down_fragment_matches")["payload"]["records"][0].__setitem__(
                "prsm_id", "missing-prsm"
            ),
            "TOP_DOWN_FRAGMENT_PRSM_NOT_FOUND",
        ),
        (
            lambda payloads: _extension(payloads, "top_down_modifications")["payload"]["records"][0].__setitem__(
                "proteoform_id", "missing-proteoform"
            ),
            "TOP_DOWN_MODIFICATION_OWNER_NOT_FOUND",
        ),
    ],
)
def test_business_validator_rejects_broken_cross_references(
    tmp_path: Path,
    mutation: object,
    expected_code: str,
) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    target = tmp_path / "top-down-v1.zp"
    convert_source_to_zp(source, target, format_version=1)

    rewrite_zp(target, mutation)  # type: ignore[arg-type]
    result = validate_zp(target, mode="deep")

    assert result.checked_blocks == 9
    assert result.issues == []
    assert result.valid is False
    assert result.top_down_valid is False
    assert expected_code in {item.code for item in result.top_down_issues}
    with pytest.raises(TopDownSchemaError):
        TopDownReader(target)


def test_business_validator_rejects_record_count_mismatch(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    target = tmp_path / "top-down-v1.zp"
    convert_source_to_zp(source, target, format_version=1)

    def mutate(payloads: dict[str, object]) -> None:
        _extension(payloads, "top_down_prsms")["payload"]["record_count"] = 99

    rewrite_zp(target, mutate)
    result = validate_zp(target, mode="deep")

    assert result.valid is False
    assert result.top_down_valid is False
    assert "TOP_DOWN_RECORD_COUNT_MISMATCH" in {
        item.code for item in result.top_down_issues
    }


def test_business_validator_rejects_invalid_interpretation_origin(tmp_path: Path) -> None:
    source = build_top_down_bundle(tmp_path / "bundle")
    target = tmp_path / "top-down-v1.zp"
    convert_source_to_zp(source, target, format_version=1)

    def mutate(payloads: dict[str, object]) -> None:
        provenance = _extension(payloads, "top_down_interpretation_provenance")
        provenance["payload"]["provenance"]["interpretation_origin"] = "invented"

    rewrite_zp(target, mutate)
    result = validate_zp(target, mode="deep")

    assert result.valid is False
    assert "TOP_DOWN_INVALID_INTERPRETATION_ORIGIN" in {
        item.code for item in result.top_down_issues
    }
