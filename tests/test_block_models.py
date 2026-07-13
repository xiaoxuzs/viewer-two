from dataclasses import fields

from binary_layer.blocks import ArrayBlock, BlockCollection, ExtensionBlock, RunBlock, SpectrumBlock


def test_spectrum_has_only_array_references() -> None:
    names = {item.name for item in fields(SpectrumBlock)}
    assert {"mz_values", "intensity_values", "mz_array", "intensity_array"}.isdisjoint(names)
    assert {"mz_array_id", "intensity_array_id"} <= names


def test_array_and_collection_queries() -> None:
    array = ArrayBlock("a", "mz", "float64", [1.0])
    spectrum = SpectrumBlock("s", "r", 1, 1, "scan=1", 0.5, None, "a", "b")
    run = RunBlock("r", "source", "run", 1, 0, 0.5, 0.5)
    blocks = BlockCollection(runs=[run], spectra=[spectrum], arrays=[array])
    assert blocks.get_array("a") is array
    assert blocks.get_spectrum("s") is spectrum
    assert blocks.get_run("r") is run
    assert blocks.extensions == []

