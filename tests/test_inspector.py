from pathlib import Path

import pytest

from binary_layer.exceptions import InvalidSourceError
from binary_layer.inspector import SourceInspector


@pytest.mark.parametrize(("name", "expected"), [
    ("a.mzML", "mock_mzml"), ("a.mzml", "mock_mzml"),
    ("a.raw", "mock_raw"), ("a.RAW", "mock_raw"), ("a.txt", "unknown"),
])
def test_extension_identification(name: str, expected: str) -> None:
    assert SourceInspector().inspect([Path(name)]).source_type == expected


def test_multiple_inputs_fail() -> None:
    with pytest.raises(InvalidSourceError):
        SourceInspector().inspect(["a.mzML", "b.mzML"])

