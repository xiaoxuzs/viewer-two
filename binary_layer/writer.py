from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .blocks import (
    ArrayBlock,
    BlockCollection,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
    StringPoolBlock,
)
from .constants import (
    BLOCK_NAMES,
    DEFAULT_ZP_WRITE_VERSION,
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ZP_WRITE_VERSIONS,
    ZP_ENDIANNESS_LITTLE,
    ZP_EXTENSION,
    ZP_MAGIC,
    ZP_VERSION,
    ZP_VERSION_V1,
    ZP_VERSION_V2,
)
from .exceptions import UnsupportedVersionError, ZpVersionNotImplementedError, ZpWriteError
from .models import BlockDirectoryEntry
from .serialization import canonical_json_bytes, to_primitive
from .v2_arrays_writer import (
    DEFAULT_V2_ARRAY_WRITE_LIMITS,
    ZpV2ArrayWriteLimits,
    prepare_v2_arrays_layout,
    write_v2_arrays_block,
)


class ZpWriter:
    def write(
        self,
        target: str | Path,
        blocks: BlockCollection,
        *,
        format_version: int = DEFAULT_ZP_WRITE_VERSION,
        v2_limits: ZpV2ArrayWriteLimits | None = None,
    ) -> Path:
        if format_version not in SUPPORTED_ZP_WRITE_VERSIONS:
            if format_version in KNOWN_ZP_VERSIONS:
                raise ZpVersionNotImplementedError(format_version, "write")
            raise UnsupportedVersionError(format_version, "write")
        if format_version == ZP_VERSION_V1:
            return self._write_v1(target, blocks)
        if format_version == ZP_VERSION_V2:
            return self._write_v2(target, blocks, v2_limits=v2_limits)
        raise UnsupportedVersionError(format_version, "write")

    def _write_v1(self, target: str | Path, blocks: BlockCollection) -> Path:
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

    def _write_v2(
        self,
        target: str | Path,
        blocks: BlockCollection,
        *,
        v2_limits: ZpV2ArrayWriteLimits | None,
    ) -> Path:
        path = Path(target)
        if path.suffix != ZP_EXTENSION:
            raise ZpWriteError(f"Output extension must be exactly {ZP_EXTENSION}: {path}")
        if not isinstance(blocks, BlockCollection):
            raise ZpWriteError("blocks must be a BlockCollection")
        limits = DEFAULT_V2_ARRAY_WRITE_LIMITS if v2_limits is None else v2_limits
        arrays_layout = prepare_v2_arrays_layout(blocks.arrays, limits=limits)
        self._validate_v2_blocks(blocks)
        payloads = self._serialize_v2_json_blocks(blocks)

        temporary = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w+b") as stream:
                stream.write(b"\x00" * HEADER_SIZE)
                entries: list[BlockDirectoryEntry] = []
                for block_name in BLOCK_NAMES:
                    offset = stream.tell()
                    if block_name == "arrays":
                        length, checksum = write_v2_arrays_block(stream, arrays_layout)
                        encoding = "zp-arrays-v2"
                    else:
                        payload = payloads[block_name]
                        stream.write(payload)
                        length = len(payload)
                        checksum = hashlib.sha256(payload).hexdigest()
                        encoding = "utf-8-json"
                    entries.append(
                        BlockDirectoryEntry(
                            block_name=block_name,
                            offset=offset,
                            length=length,
                            encoding=encoding,
                            checksum=checksum,
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
                        ZP_VERSION_V2,
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

    @staticmethod
    def _serialize_v2_json_blocks(blocks: BlockCollection) -> dict[str, bytes]:
        global_meta = to_primitive(blocks.global_meta)
        if not isinstance(global_meta, dict):
            raise ZpWriteError("global_meta must serialize to an object")
        global_meta["format_version"] = ZP_VERSION_V2
        logical_blocks = {
            "global_meta": global_meta,
            "string_pool": blocks.string_pool,
            "core_runs": blocks.runs,
            "core_spectra": blocks.spectra,
            "core_precursors": blocks.precursors,
            "core_chromatograms": blocks.chromatograms,
            "indexes": blocks.indexes,
            "extensions": blocks.extensions,
        }
        try:
            return {name: canonical_json_bytes(value) for name, value in logical_blocks.items()}
        except (TypeError, ValueError) as exc:
            raise ZpWriteError(f"A required logical block is not serializable: {exc}") from exc

    @classmethod
    def _validate_v2_blocks(cls, blocks: BlockCollection) -> None:
        if blocks.global_meta is None or blocks.string_pool is None or blocks.indexes is None:
            raise ZpWriteError("global_meta, string_pool, and indexes must be built before writing")
        singleton_types = (
            (blocks.global_meta, GlobalMetaBlock, "global_meta"),
            (blocks.string_pool, StringPoolBlock, "string_pool"),
            (blocks.indexes, IndexBlock, "indexes"),
        )
        for value, expected, name in singleton_types:
            if not isinstance(value, expected):
                raise ZpWriteError(f"{name} must be a {expected.__name__}")
        list_types = (
            (blocks.runs, RunBlock, "core_runs"),
            (blocks.spectra, SpectrumBlock, "core_spectra"),
            (blocks.precursors, PrecursorBlock, "core_precursors"),
            (blocks.chromatograms, ChromatogramBlock, "core_chromatograms"),
            (blocks.arrays, ArrayBlock, "arrays"),
            (blocks.extensions, ExtensionBlock, "extensions"),
        )
        for values, expected, name in list_types:
            if not isinstance(values, list):
                raise ZpWriteError(f"{name} must be a list")
            for position, value in enumerate(values):
                if not isinstance(value, expected):
                    raise ZpWriteError(f"{name}[{position}] must be a {expected.__name__}")

        meta = blocks.global_meta
        counts = {
            "run_count": len(blocks.runs),
            "spectrum_count": len(blocks.spectra),
            "chromatogram_count": len(blocks.chromatograms),
            "array_count": len(blocks.arrays),
        }
        for field, actual in counts.items():
            if getattr(meta, field) != actual:
                raise ZpWriteError(f"global_meta.{field} does not match the logical blocks")

        run_map = cls._unique_map(blocks.runs, "run_id", "core_runs")
        spectrum_map = cls._unique_map(blocks.spectra, "spectrum_id", "core_spectra")
        precursor_map = cls._unique_map(blocks.precursors, "precursor_id", "core_precursors")
        cls._unique_map(blocks.chromatograms, "chromatogram_id", "core_chromatograms")
        array_map = cls._unique_map(blocks.arrays, "array_id", "arrays")

        for run in blocks.runs:
            spectrum_count = sum(item.run_id == run.run_id for item in blocks.spectra)
            chromatogram_count = sum(item.run_id == run.run_id for item in blocks.chromatograms)
            if run.spectrum_count != spectrum_count or run.chromatogram_count != chromatogram_count:
                raise ZpWriteError(f"Run {run.run_id} counts do not match referenced logical blocks")
        for spectrum in blocks.spectra:
            if spectrum.run_id not in run_map:
                raise ZpWriteError(f"Spectrum {spectrum.spectrum_id} references missing run {spectrum.run_id}")
            mz_array = array_map.get(spectrum.mz_array_id)
            intensity_array = array_map.get(spectrum.intensity_array_id)
            if mz_array is None or mz_array.array_type != "mz":
                raise ZpWriteError(f"Spectrum {spectrum.spectrum_id} has an invalid m/z array reference")
            if intensity_array is None or intensity_array.array_type != "intensity":
                raise ZpWriteError(f"Spectrum {spectrum.spectrum_id} has an invalid intensity array reference")
            if len(mz_array.values) != len(intensity_array.values):
                raise ZpWriteError(f"Spectrum {spectrum.spectrum_id} arrays have different lengths")
            if spectrum.precursor_id is not None:
                precursor = precursor_map.get(spectrum.precursor_id)
                if precursor is None or precursor.spectrum_id != spectrum.spectrum_id:
                    raise ZpWriteError(f"Spectrum {spectrum.spectrum_id} has an invalid precursor reference")
        for precursor in blocks.precursors:
            spectrum = spectrum_map.get(precursor.spectrum_id)
            if spectrum is None or spectrum.precursor_id != precursor.precursor_id:
                raise ZpWriteError(f"Precursor {precursor.precursor_id} has an invalid spectrum reference")
        for chromatogram in blocks.chromatograms:
            if chromatogram.run_id not in run_map:
                raise ZpWriteError(f"Chromatogram {chromatogram.chromatogram_id} references a missing run")
            time_array = array_map.get(chromatogram.time_array_id)
            intensity_array = array_map.get(chromatogram.intensity_array_id)
            if time_array is None or time_array.array_type != "time":
                raise ZpWriteError(f"Chromatogram {chromatogram.chromatogram_id} has an invalid time array reference")
            if intensity_array is None or intensity_array.array_type != "intensity":
                raise ZpWriteError(f"Chromatogram {chromatogram.chromatogram_id} has an invalid intensity array reference")
            if len(time_array.values) != len(intensity_array.values):
                raise ZpWriteError(f"Chromatogram {chromatogram.chromatogram_id} arrays have different lengths")

        if not isinstance(blocks.string_pool.strings, list) or any(
            not isinstance(value, str) for value in blocks.string_pool.strings
        ):
            raise ZpWriteError("string_pool.strings must be a list of strings")
        cls._validate_indexes(blocks.indexes, blocks.spectra, spectrum_map)

    @staticmethod
    def _unique_map(values: list[object], field: str, block_name: str) -> dict[str, object]:
        result: dict[str, object] = {}
        for value in values:
            identifier = getattr(value, field)
            if not isinstance(identifier, str) or not identifier:
                raise ZpWriteError(f"{block_name}.{field} values must be nonempty strings")
            if identifier in result:
                raise ZpWriteError(f"Duplicate {block_name}.{field}: {identifier}")
            result[identifier] = value
        return result

    @staticmethod
    def _validate_indexes(indexes: IndexBlock, spectra: list[SpectrumBlock], spectrum_map: dict[str, object]) -> None:
        for name in ("scan_index", "rt_index", "spectrum_id_index"):
            records = getattr(indexes, name)
            if not isinstance(records, list):
                raise ZpWriteError(f"indexes.{name} must be a list")
            for record in records:
                if not isinstance(record, dict) or record.get("spectrum_id") not in spectrum_map:
                    raise ZpWriteError(f"indexes.{name} references a missing spectrum")
        for record in indexes.spectrum_id_index:
            position = record.get("position")
            spectrum_id = record.get("spectrum_id")
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or position < 0
                or position >= len(spectra)
                or spectra[position].spectrum_id != spectrum_id
            ):
                raise ZpWriteError("indexes.spectrum_id_index contains an invalid position")
