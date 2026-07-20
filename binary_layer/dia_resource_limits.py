from __future__ import annotations

from .v2_arrays_reader import ZpV2ArrayReadLimits
from .v2_arrays_writer import ZpV2ArrayWriteLimits
from .v2_validator import ZpV2ValidationLimits


# The frozen type-A run has 219,532 arrays and about 1.29 GiB of float64
# payload.  These explicit limits keep the generic defaults conservative while
# allowing the inspected production profile to use the unchanged v2 layout.
DIA_V2_ARRAY_WRITE_LIMITS = ZpV2ArrayWriteLimits(
    max_arrays_block_length=2 * 1024 * 1024 * 1024,
    max_directory_length=64 * 1024 * 1024,
    max_entry_count=300_000,
    max_array_value_count=16_000_000,
    max_array_id_utf8_length=4096,
    max_payload_length=2 * 1024 * 1024 * 1024,
)

DIA_V2_ARRAY_READ_LIMITS = ZpV2ArrayReadLimits(
    max_arrays_block_length=2 * 1024 * 1024 * 1024,
    max_directory_length=64 * 1024 * 1024,
    max_entry_count=300_000,
    max_array_value_count=16_000_000,
    max_array_id_utf8_length=4096,
    max_payload_length=2 * 1024 * 1024 * 1024,
    max_decoded_memory=2 * 1024 * 1024 * 1024,
)

DIA_V2_VALIDATION_LIMITS = ZpV2ValidationLimits(
    max_arrays_block_length=2 * 1024 * 1024 * 1024,
    max_top_directory_length=64 * 1024 * 1024,
    max_array_directory_length=64 * 1024 * 1024,
    max_entry_count=300_000,
    max_array_value_count=16_000_000,
    max_array_id_utf8_length=4096,
    max_payload_length=2 * 1024 * 1024 * 1024,
    max_work_memory=2 * 1024 * 1024 * 1024,
    chunk_size=256 * 1024,
)
