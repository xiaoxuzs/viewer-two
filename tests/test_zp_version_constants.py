from binary_layer import (
    DEFAULT_ZP_WRITE_VERSION,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ZP_READ_VERSIONS,
    SUPPORTED_ZP_VALIDATE_VERSIONS,
    SUPPORTED_ZP_WRITE_VERSIONS,
    ZP_VERSION,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
    ZpReader,
    ZpValidator,
    ZpWriter,
)


def test_version_constants_distinguish_known_from_implemented_versions() -> None:
    assert ZP_VERSION == ZP_VERSION_V1 == DEFAULT_ZP_WRITE_VERSION == 1
    assert ZP_VERSION_V2 == 2
    assert KNOWN_ZP_VERSIONS == frozenset({1, 2})
    assert SUPPORTED_ZP_READ_VERSIONS == frozenset({1, 2})
    assert SUPPORTED_ZP_WRITE_VERSIONS == frozenset({1, 2})
    assert SUPPORTED_ZP_VALIDATE_VERSIONS == frozenset({1, 2})
    assert all(item is not None for item in (ZpReader, ZpWriter, ZpValidator))
