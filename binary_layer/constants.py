import struct

ZP_MAGIC = b"ZPMS"
ZP_VERSION = 1
ZP_VERSION_V1 = 1
ZP_VERSION_V2 = 2
DEFAULT_ZP_WRITE_VERSION = ZP_VERSION_V1
KNOWN_ZP_VERSIONS = frozenset({ZP_VERSION_V1, ZP_VERSION_V2})
SUPPORTED_ZP_READ_VERSIONS = frozenset({ZP_VERSION_V1, ZP_VERSION_V2})
SUPPORTED_ZP_WRITE_VERSIONS = frozenset({ZP_VERSION_V1, ZP_VERSION_V2})
SUPPORTED_ZP_VALIDATE_VERSIONS = frozenset({ZP_VERSION_V1, ZP_VERSION_V2})
ZP_EXTENSION = ".zp"
ZP_ENDIANNESS_LITTLE = 1

BLOCK_NAMES = (
    "global_meta",
    "string_pool",
    "core_runs",
    "core_spectra",
    "core_precursors",
    "core_chromatograms",
    "arrays",
    "indexes",
    "extensions",
)
REQUIRED_BLOCK_NAMES = frozenset(BLOCK_NAMES)
SUPPORTED_ENCODINGS = frozenset({"json"})
SUPPORTED_ARRAY_TYPES = frozenset({"mz", "intensity", "time"})
SUPPORTED_DTYPES = frozenset({"float64"})

HEADER_STRUCT = struct.Struct("<4sHBBQQ")
HEADER_SIZE = HEADER_STRUCT.size
DIRECTORY_LENGTH_STRUCT = struct.Struct("<Q")
