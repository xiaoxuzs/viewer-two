from __future__ import annotations

from pathlib import Path

from binary_layer.inspector import SourceInspector
from binary_layer.models import PipelineContext
from binary_layer.mzml_schema import MzmlMetadataV1
from binary_layer.tools.real_mzml import RealMzmlParseTool

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_real_tool_builds_ms2_spectrum_and_bidirectional_precursor() -> None:
    path = FIXTURE_DIR / "accept_indexed_float64_zlib.mzML"
    context = PipelineContext(SourceInspector().inspect([path]), metadata={"input_sha256": "d" * 64})

    RealMzmlParseTool().run(context)

    assert [item.ms_level for item in context.blocks.spectra] == [1, 2]
    ms1, ms2 = context.blocks.spectra
    assert ms1.precursor_id is None
    assert ms2.precursor_id == "spectrum_000002:precursor"
    assert len(context.blocks.precursors) == 1
    precursor = context.blocks.precursors[0]
    assert precursor.precursor_id == ms2.precursor_id
    assert precursor.spectrum_id == ms2.spectrum_id
    assert precursor.precursor_mz == 445.2
    assert precursor.charge == 2
    assert precursor.intensity == 50.0
    assert len(context.blocks.arrays) == 4
    metadata = MzmlMetadataV1.from_payload(context.blocks.extensions[0].payload)
    assert metadata.spectra[1].precursor_source_spectrum_ref == "controllerType=0 controllerNumber=1 scan=1"
    assert metadata.spectra[1].isolation_window_target_mz == 445.2
    assert metadata.spectra[1].activation_methods[0].accession == "MS:1000133"
    assert metadata.spectra[1].collision_energy == 25.0
    assert metadata.spectra[1].collision_energy_unit_name == "electronvolt"


def test_complete_precursor_metadata_fixture_preserves_source_values() -> None:
    path = FIXTURE_DIR / "accept_ms2_precursor_metadata.mzML"
    context = PipelineContext(SourceInspector().inspect([path]), metadata={"input_sha256": "e" * 64})

    RealMzmlParseTool().run(context)

    precursor = context.blocks.precursors[0]
    assert precursor.precursor_mz == 678.9
    assert precursor.charge == 3
    assert precursor.intensity == 1234.5
    metadata = MzmlMetadataV1.from_payload(context.blocks.extensions[0].payload)
    ms2 = metadata.spectra[1]
    assert ms2.precursor_source_spectrum_ref == "controllerType=0 controllerNumber=1 scan=1"
    assert ms2.isolation_window_target_mz == 679.0
    assert ms2.isolation_window_lower_offset == 0.7
    assert ms2.isolation_window_upper_offset == 1.3
    assert [(item.accession, item.name) for item in ms2.activation_methods] == [
        ("MS:1000422", "beam-type collision-induced dissociation")
    ]
    assert ms2.collision_energy == 31.5
    assert ms2.collision_energy_unit_accession == "UO:0000266"
    assert ms2.collision_energy_unit_name == "electronvolt"
