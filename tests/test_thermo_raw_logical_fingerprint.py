from binary_layer.logical_comparison import build_extension_filtered_logical_fingerprint
from binary_layer.logical_fingerprint import LogicalArrayFingerprint, build_logical_fingerprint


def _blocks() -> dict[str, object]:
    return {
        "global_meta": {"format_version": 2, "source": "same"},
        "string_pool": {"strings": []},
        "core_runs": [],
        "core_spectra": [],
        "core_precursors": [],
        "core_chromatograms": [],
        "indexes": {},
        "extensions": [{"extension_type": "mzml_metadata", "extension_version": "1", "payload": {}}],
    }


def test_extension_filtered_fingerprint_is_generic_and_full_fingerprint_is_unchanged() -> None:
    arrays = [LogicalArrayFingerprint("a", "mz", 1, "a" * 64)]
    direct = _blocks()
    raw = _blocks()
    raw["extensions"] = [
        *raw["extensions"],  # type: ignore[misc]
        {
            "extension_type": "thermo_raw_conversion_metadata",
            "extension_version": "1",
            "payload": {"source_kind": "thermo_raw"},
        },
    ]

    assert build_logical_fingerprint(direct, arrays).sha256 != build_logical_fingerprint(raw, arrays).sha256
    assert build_extension_filtered_logical_fingerprint(
        direct,
        arrays,
        excluded_extension_types={"thermo_raw_conversion_metadata"},
    ).sha256 == build_extension_filtered_logical_fingerprint(
        raw,
        arrays,
        excluded_extension_types={"thermo_raw_conversion_metadata"},
    ).sha256
