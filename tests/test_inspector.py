from pathlib import Path

import pytest

from binary_layer.exceptions import InvalidSourceError
from binary_layer.inspector import SourceInspector


@pytest.mark.parametrize(("name", "expected"), [
    ("a.mzML", "real_mzml"), ("a.mzml", "real_mzml"),
    ("a.MZML", "real_mzml"), ("a.MzMl", "real_mzml"),
    ("a.raw", "real_thermo_raw"), ("a.RAW", "real_thermo_raw"), ("a.txt", "unknown"),
])
def test_extension_identification(name: str, expected: str) -> None:
    assert SourceInspector().inspect([Path(name)]).source_type == expected


def test_multiple_inputs_fail() -> None:
    with pytest.raises(InvalidSourceError):
        SourceInspector().inspect(["a.mzML", "b.mzML"])


def test_classification_depends_only_on_extension(tmp_path: Path) -> None:
    mzml = tmp_path / "same.mzML"
    raw = tmp_path / "same.raw"
    unknown = tmp_path / "same.txt"
    for path in (mzml, raw, unknown):
        path.write_bytes(b"identical bytes that are not parsed")

    inspector = SourceInspector()
    assert inspector.inspect([mzml]).source_type == "real_mzml"
    raw_profile = inspector.inspect([raw])
    assert raw_profile.source_type == "real_thermo_raw"
    assert raw_profile.path == raw
    assert raw_profile.suffix == ".raw"
    assert raw_profile.file_size == raw.stat().st_size
    assert inspector.inspect([unknown]).source_type == "unknown"


def test_inspector_never_derives_mock_mzml_from_normal_mzml_extension() -> None:
    for name in ("sample.mzML", "sample.mzml", "sample.MZML", "sample.MzMl"):
        assert SourceInspector().inspect([name]).source_type != "mock_mzml"


def test_inspector_never_derives_mock_raw_from_real_raw_extension() -> None:
    for name in ("sample.raw", "sample.RAW", "sample.Raw"):
        assert SourceInspector().inspect([name]).source_type != "mock_raw"
