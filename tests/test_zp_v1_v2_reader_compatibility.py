from __future__ import annotations

from pathlib import Path

import pytest

from binary_layer import ZpReader


FIXTURE_DIR = Path(__file__).parents[1] / "specs" / "zp_full" / "fixtures"


@pytest.mark.parametrize("kind", ["full", "minimal"])
def test_public_reader_api_has_v1_v2_logical_parity(kind: str) -> None:
    readers = {
        version: ZpReader(FIXTURE_DIR / f"valid_{kind}_v{version}.zp")
        for version in (1, 2)
    }
    assert [reader.read_header().version for reader in readers.values()] == [1, 2]
    assert [item.block_name for item in readers[1].read_directory()] == [
        item.block_name for item in readers[2].read_directory()
    ]
    assert readers[1].read_spectra() == readers[2].read_spectra()
    assert readers[1].read_spectrum("spectrum_000001") == readers[2].read_spectrum("spectrum_000001")
    assert readers[1].read_spectrum_arrays("spectrum_000001") == readers[2].read_spectrum_arrays("spectrum_000001")
    assert sorted(readers[1].read_arrays(), key=lambda item: item.array_id) == sorted(
        readers[2].read_arrays(), key=lambda item: item.array_id
    )
    assert sorted(readers[1].read_block("arrays"), key=lambda item: item["array_id"]) == sorted(
        readers[2].read_block("arrays"), key=lambda item: item["array_id"]
    )
    assert readers[1].read_chromatograms() == readers[2].read_chromatograms()
    if kind == "full":
        assert readers[1].read_precursors() == readers[2].read_precursors()
        assert readers[1].read_chromatogram_arrays("chromatogram_000001") == readers[2].read_chromatogram_arrays(
            "chromatogram_000001"
        )
