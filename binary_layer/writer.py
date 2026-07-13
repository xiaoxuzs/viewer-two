from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .blocks import BlockCollection
from .constants import (
    BLOCK_NAMES,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    ZP_ENDIANNESS_LITTLE,
    ZP_EXTENSION,
    ZP_MAGIC,
    ZP_VERSION,
)
from .exceptions import ZpWriteError
from .models import BlockDirectoryEntry
from .serialization import canonical_json_bytes


class ZpWriter:
    def write(self, target: str | Path, blocks: BlockCollection) -> Path:
        path = Path(target)
        if path.suffix != ZP_EXTENSION:
            raise ZpWriteError(f"Output extension must be exactly {ZP_EXTENSION}: {path}")
        if not isinstance(blocks, BlockCollection):
            raise ZpWriteError("blocks must be a BlockCollection")
        if blocks.global_meta is None or blocks.string_pool is None or blocks.indexes is None:
            raise ZpWriteError("global_meta, string_pool, and indexes must be built before writing")
        list_blocks = (blocks.runs, blocks.spectra, blocks.precursors, blocks.chromatograms, blocks.arrays, blocks.extensions)
        if any(not isinstance(value, list) for value in list_blocks):
            raise ZpWriteError("core collection blocks, arrays, and extensions must be lists")

        payloads = self._serialize_blocks(blocks)
        temporary = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w+b") as stream:
                stream.write(b"\x00" * HEADER_SIZE)
                entries: list[BlockDirectoryEntry] = []
                for block_name in BLOCK_NAMES:
                    payload = payloads[block_name]
                    offset = stream.tell()
                    stream.write(payload)
                    entries.append(
                        BlockDirectoryEntry(
                            block_name=block_name,
                            offset=offset,
                            length=len(payload),
                            encoding="json",
                            checksum=hashlib.sha256(payload).hexdigest(),
                        )
                    )
                directory_offset = stream.tell()
                directory_payload = canonical_json_bytes(entries)
                stream.write(DIRECTORY_LENGTH_STRUCT.pack(len(directory_payload)))
                stream.write(directory_payload)
                created_at = int(time.time() * 1000)
                stream.seek(0)
                stream.write(
                    HEADER_STRUCT.pack(
                        ZP_MAGIC,
                        ZP_VERSION,
                        ZP_ENDIANNESS_LITTLE,
                        0,
                        created_at,
                        directory_offset,
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return path
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, ZpWriteError):
                raise
            raise ZpWriteError(f"Failed to write {path}: {exc}") from exc

    @staticmethod
    def _serialize_blocks(blocks: BlockCollection) -> dict[str, bytes]:
        logical_blocks = {
            "global_meta": blocks.global_meta,
            "string_pool": blocks.string_pool,
            "core_runs": blocks.runs,
            "core_spectra": blocks.spectra,
            "core_precursors": blocks.precursors,
            "core_chromatograms": blocks.chromatograms,
            "arrays": blocks.arrays,
            "indexes": blocks.indexes,
            "extensions": blocks.extensions,
        }
        try:
            return {name: canonical_json_bytes(logical_blocks[name]) for name in BLOCK_NAMES}
        except (TypeError, ValueError) as exc:
            raise ZpWriteError(f"A required logical block is not serializable: {exc}") from exc
