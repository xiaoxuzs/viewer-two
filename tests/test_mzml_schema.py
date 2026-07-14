from __future__ import annotations

import json
from dataclasses import fields

import numpy as np
import pytest

from binary_layer.blocks import PrecursorBlock, SpectrumBlock
from binary_layer.exceptions import MzmlSchemaError
from binary_layer.mzml_schema import (
    ArrayCompression,
    AuxiliaryArrayV1,
    ChromatogramMetadataV1,
    ChromatogramType,
    CvParamV1,
    MZML_EXTENSION_SCHEMA_VERSION,
    MzmlAuxiliaryArraysV1,
    MzmlMetadataV1,
    MzmlRunMetadataV1,
    MzmlSourceMetadataV1,
    NumericDtype,
    OwnerKind,
    Polarity,
    SpectrumMetadataV1,
    SpectrumRepresentation,
    TraceableEntityV1,
)
from binary_layer.serialization import canonical_json_bytes


def spectrum_metadata() -> SpectrumMetadataV1:
    return SpectrumMetadataV1(
        spectrum_id="spectrum_1",
        polarity=Polarity.POSITIVE,
        representation=SpectrumRepresentation.CENTROID,
        default_array_length=2,
        total_ion_current=30.0,
        base_peak_mz=200.0,
        base_peak_intensity=20.0,
        lowest_observed_mz=100.0,
        highest_observed_mz=200.0,
        scan_window_lower=50.0,
        scan_window_upper=500.0,
        filter_string="fixture filter",
        instrument_configuration_ref="IC1",
        data_processing_ref="DP1",
        precursor_source_spectrum_ref=None,
        isolation_window_target_mz=None,
        isolation_window_lower_offset=None,
        isolation_window_upper_offset=None,
        activation_methods=(),
        collision_energy=None,
        collision_energy_unit_accession=None,
        collision_energy_unit_name=None,
        source_mz_dtype=NumericDtype.FLOAT64,
        source_intensity_dtype=NumericDtype.FLOAT64,
        source_mz_compression=ArrayCompression.ZLIB,
        source_intensity_compression=ArrayCompression.ZLIB,
        source_rt_value=0.5,
        source_rt_unit_accession="UO:0000031",
        source_rt_unit_name="minute",
    )


def metadata() -> MzmlMetadataV1:
    entity = TraceableEntityV1(
        id="fixture_writer",
        accession="MS:1000799",
        name="custom unreleased software tool",
        version="1.0",
        cv_params=(CvParamV1("MS:1000799", "custom unreleased software tool", "fixture"),),
    )
    chromatogram = ChromatogramMetadataV1(
        chromatogram_id="TIC",
        chromatogram_type=ChromatogramType.TIC,
        default_array_length=2,
        data_processing_ref="DP1",
        source_time_dtype=NumericDtype.FLOAT64,
        source_intensity_dtype=NumericDtype.FLOAT64,
        source_time_compression=ArrayCompression.ZLIB,
        source_intensity_compression=ArrayCompression.ZLIB,
        source_time_unit_accession="UO:0000010",
        source_time_unit_name="second",
    )
    return MzmlMetadataV1(
        source=MzmlSourceMetadataV1(True, "1.1.0", "MS:1000768", "Thermo nativeID format"),
        run=MzmlRunMetadataV1("run1", "IC1", "SF1", None, None),
        spectra=(spectrum_metadata(),),
        chromatograms=(chromatogram,),
        instruments=(),
        software=(entity,),
        data_processing=(TraceableEntityV1("DP1", None, None, None, ()),),
    )


def auxiliary_array() -> AuxiliaryArrayV1:
    return AuxiliaryArrayV1(
        owner_kind=OwnerKind.CHROMATOGRAM,
        owner_id="TIC",
        array_accession="MS:1000786",
        array_name="ms level",
        dtype=NumericDtype.INT64,
        values=(1, 2),
        unit_accession="UO:0000186",
        unit_name="dimensionless unit",
    )


def test_metadata_v1_roundtrip_uses_only_json_primitives() -> None:
    value = metadata()
    payload = value.to_payload()
    encoded = canonical_json_bytes(payload)
    decoded = json.loads(encoded.decode("utf-8"))
    assert MzmlMetadataV1.from_payload(decoded) == value
    assert payload["schema_version"] == MZML_EXTENSION_SCHEMA_VERSION == 1


def test_auxiliary_arrays_v1_roundtrip_and_whitelist() -> None:
    value = MzmlAuxiliaryArraysV1((auxiliary_array(),))
    payload = value.to_payload()
    assert MzmlAuxiliaryArraysV1.from_payload(json.loads(canonical_json_bytes(payload))) == value
    assert payload["arrays"][0]["values"] == [1, 2]


@pytest.mark.parametrize("schema_type,value", [
    (MzmlMetadataV1, metadata()),
    (MzmlAuxiliaryArraysV1, MzmlAuxiliaryArraysV1((auxiliary_array(),))),
])
def test_unknown_top_level_field_is_rejected(schema_type, value) -> None:
    payload = value.to_payload()
    payload["library_private"] = {}
    with pytest.raises(MzmlSchemaError, match="unknown fields"):
        schema_type.from_payload(payload)


@pytest.mark.parametrize("schema_type,value", [
    (MzmlMetadataV1, metadata()),
    (MzmlAuxiliaryArraysV1, MzmlAuxiliaryArraysV1((auxiliary_array(),))),
])
def test_wrong_schema_version_is_rejected(schema_type, value) -> None:
    payload = value.to_payload()
    payload["schema_version"] = 2
    with pytest.raises(MzmlSchemaError, match="unsupported version"):
        schema_type.from_payload(payload)


def test_wrong_enum_is_rejected() -> None:
    payload = metadata().to_payload()
    payload["spectra"][0]["polarity"] = "sideways"
    with pytest.raises(MzmlSchemaError, match="unsupported value"):
        MzmlMetadataV1.from_payload(payload)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_metadata_numbers_are_rejected(bad: float) -> None:
    payload = metadata().to_payload()
    payload["spectra"][0]["total_ion_current"] = bad
    with pytest.raises(MzmlSchemaError, match="finite"):
        MzmlMetadataV1.from_payload(payload)


def test_numpy_values_and_mixed_auxiliary_values_are_rejected() -> None:
    with pytest.raises(MzmlSchemaError, match="plain Python integers"):
        AuxiliaryArrayV1(OwnerKind.CHROMATOGRAM, "TIC", "MS:1000786", "ms level", NumericDtype.INT64, (np.int64(1),), "UO:0000186", "dimensionless unit")
    with pytest.raises(MzmlSchemaError, match="plain Python integers"):
        AuxiliaryArrayV1(OwnerKind.CHROMATOGRAM, "TIC", "MS:1000786", "ms level", NumericDtype.INT64, (1, 2.0), "UO:0000186", "dimensionless unit")


@pytest.mark.parametrize("bad_values", [("AQID",), (b"compressed",), ({"library": "object"},)])
def test_encoded_bytes_text_and_nested_objects_are_not_auxiliary_values(bad_values: tuple[object, ...]) -> None:
    with pytest.raises(MzmlSchemaError, match="plain Python integers"):
        AuxiliaryArrayV1(OwnerKind.CHROMATOGRAM, "TIC", "MS:1000786", "ms level", NumericDtype.INT64, bad_values, "UO:0000186", "dimensionless unit")  # type: ignore[arg-type]


def test_unknown_auxiliary_accession_or_name_is_rejected() -> None:
    with pytest.raises(MzmlSchemaError, match="unsupported auxiliary array"):
        AuxiliaryArrayV1(OwnerKind.CHROMATOGRAM, "TIC", "MS:1000786", "vendor mystery", NumericDtype.INT64, (1,), "UO:0000186", "dimensionless unit")
    with pytest.raises(MzmlSchemaError, match="unsupported auxiliary array"):
        AuxiliaryArrayV1(OwnerKind.SPECTRUM, "s1", "MS:1000786", "ms level", NumericDtype.INT64, (1,), "UO:0000186", "dimensionless unit")


def test_raw_parser_dict_is_not_a_schema_object() -> None:
    with pytest.raises(MzmlSchemaError, match="MzmlSourceMetadataV1"):
        MzmlMetadataV1(  # type: ignore[arg-type]
            source={"id": "pyteomics dictionary"},
            run=MzmlRunMetadataV1("run1", None, None, None, None),
            spectra=(), chromatograms=(), instruments=(), software=(), data_processing=(),
        )


def test_extension_schemas_do_not_change_core_block_fields() -> None:
    assert [item.name for item in fields(SpectrumBlock)] == [
        "spectrum_id", "run_id", "ms_level", "scan_number", "native_id", "rt",
        "precursor_id", "mz_array_id", "intensity_array_id",
    ]
    assert [item.name for item in fields(PrecursorBlock)] == [
        "precursor_id", "spectrum_id", "precursor_mz", "charge", "intensity",
    ]
