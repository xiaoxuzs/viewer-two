from __future__ import annotations

import importlib.util
from pathlib import Path

from binary_layer.mzml_adapter import parse_mzml

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mzml"


def test_adapter_extracts_complete_ms2_precursor_facts_and_metadata() -> None:
    document = parse_mzml(FIXTURE_DIR / "accept_indexed_float64_zlib.mzML")

    assert [item.ms_level for item in document.spectra] == [1, 2]
    assert document.spectra[0].precursors == ()
    ms2 = document.spectra[1]
    assert len(ms2.precursors) == 1
    precursor = ms2.precursors[0]
    assert precursor.selected_ion_count == 1
    assert precursor.source_spectrum_ref == "controllerType=0 controllerNumber=1 scan=1"
    assert precursor.selected_ion_mz == 445.2
    assert precursor.charge == 2
    assert type(precursor.charge) is int
    assert precursor.charge_present is True
    assert precursor.charge_explicit_zero is False
    assert precursor.intensity == 50.0
    assert precursor.isolation_target_mz == 445.2
    assert precursor.isolation_lower_offset == 1.0
    assert precursor.isolation_upper_offset == 1.0
    assert [(item.accession, item.name) for item in precursor.activation_methods] == [
        ("MS:1000133", "collision-induced dissociation")
    ]
    assert precursor.collision_energy == 25.0
    assert precursor.collision_energy_unit_accession == "UO:0000266"
    assert precursor.collision_energy_unit_name == "electronvolt"
    assert not hasattr(precursor, "keys")

    assert document.metadata_schema is not None
    metadata = document.metadata_schema.spectra[1]
    assert metadata.precursor_source_spectrum_ref == precursor.source_spectrum_ref
    assert metadata.isolation_window_target_mz == precursor.isolation_target_mz
    assert metadata.isolation_window_lower_offset == precursor.isolation_lower_offset
    assert metadata.isolation_window_upper_offset == precursor.isolation_upper_offset
    assert metadata.activation_methods == precursor.activation_methods
    assert metadata.collision_energy == precursor.collision_energy
    assert metadata.collision_energy_unit_accession == precursor.collision_energy_unit_accession
    assert metadata.collision_energy_unit_name == precursor.collision_energy_unit_name


def test_missing_charge_and_explicit_zero_remain_distinguishable(tmp_path: Path) -> None:
    missing = parse_mzml(FIXTURE_DIR / "reject_missing_charge.mzML").spectra[1].precursors[0]

    script = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("fixture_builder_b4", script)
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    spectra = [builder.spectrum(0, ms_level=1), builder.spectrum(1, ms_level=2, charge=0)]
    path = tmp_path / "zero_charge.mzML"
    path.write_text(builder.mzml_document("zero_charge", spectra, []), encoding="utf-8")
    zero = parse_mzml(path).spectra[1].precursors[0]

    assert missing.charge is None
    assert missing.charge_present is False
    assert missing.charge_explicit_zero is False
    assert zero.charge is None
    assert zero.charge_present is True
    assert zero.charge_explicit_zero is True


def test_adapter_never_selects_first_of_multiple_precursors_or_selected_ions() -> None:
    multiple_precursors = parse_mzml(FIXTURE_DIR / "reject_multiple_precursors.mzML").spectra[1]
    multiple_ions = parse_mzml(FIXTURE_DIR / "reject_multiple_selected_ions.mzML").spectra[1]

    assert len(multiple_precursors.precursors) == 2
    assert multiple_precursors.precursor_count == 2
    assert len(multiple_ions.precursors) == 1
    assert multiple_ions.precursors[0].selected_ion_count == 2
    assert multiple_ions.selected_ion_count == 2
    assert multiple_ions.selected_ion_mz is None

