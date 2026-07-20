from __future__ import annotations

from copy import deepcopy
from typing import Iterable, Mapping

from .logical_fingerprint import (
    LogicalArrayFingerprint,
    LogicalFingerprint,
    build_logical_fingerprint,
)


def build_extension_filtered_logical_fingerprint(
    blocks: Mapping[str, object],
    arrays: Iterable[LogicalArrayFingerprint],
    *,
    excluded_extension_types: Iterable[str],
) -> LogicalFingerprint:
    """Build a comparison fingerprint while excluding named provenance Extensions.

    The complete fingerprint API remains unchanged and continues to include every
    Extension. This generic helper is only for explicitly scoped comparisons.
    """
    filtered_blocks = {name: deepcopy(value) for name, value in blocks.items()}
    extensions = filtered_blocks.get("extensions")
    excluded = frozenset(excluded_extension_types)
    if not isinstance(extensions, list):
        raise ValueError("extensions must be a list")
    filtered_blocks["extensions"] = [
        item
        for item in extensions
        if not isinstance(item, dict) or item.get("extension_type") not in excluded
    ]
    return build_logical_fingerprint(filtered_blocks, arrays)
