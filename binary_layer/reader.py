from __future__ import annotations

from pathlib import Path
from typing import Any

from .blocks import (
    ArrayBlock,
    ChromatogramBlock,
    ExtensionBlock,
    GlobalMetaBlock,
    IndexBlock,
    PrecursorBlock,
    RunBlock,
    SpectrumBlock,
)
from .constants import (
    DIRECTORY_LENGTH_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    KNOWN_ZP_VERSIONS,
    SUPPORTED_ZP_READ_VERSIONS,
    SUPPORTED_ENCODINGS,
    ZP_ENDIANNESS_LITTLE,
    ZP_MAGIC,
)
from .exceptions import UnsupportedVersionError, ZpReadError, ZpVersionNotImplementedError
from .models import BlockDirectoryEntry, ZpHeader
from .serialization import parse_json_bytes, parse_utc_datetime


class ZpReader:
    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)

    def read_header(self) -> ZpHeader:
        try:
            with self.file_path.open("rb") as stream:
                raw = stream.read(HEADER_SIZE)
        except OSError as exc:
            raise ZpReadError(f"Cannot read {self.file_path}: {exc}") from exc
        if len(raw) != HEADER_SIZE:
            raise ZpReadError("File is shorter than the fixed 24-byte header")
        header = ZpHeader(*HEADER_STRUCT.unpack(raw))
        if header.magic != ZP_MAGIC:
            raise ZpReadError(f"Invalid magic: {header.magic!r}")
        if header.endianness != ZP_ENDIANNESS_LITTLE:
            raise ZpReadError(f"Unsupported endianness: {header.endianness}")
        if header.version not in SUPPORTED_ZP_READ_VERSIONS:
            if header.version in KNOWN_ZP_VERSIONS:
                raise ZpVersionNotImplementedError(header.version, "read")
            raise UnsupportedVersionError(header.version, "read")
        return header

    def read_directory(self) -> list[BlockDirectoryEntry]:
        header = self.read_header()
        try:
            with self.file_path.open("rb") as stream:
                stream.seek(header.directory_offset)
                length_raw = stream.read(DIRECTORY_LENGTH_STRUCT.size)
                if len(length_raw) != DIRECTORY_LENGTH_STRUCT.size:
                    raise ZpReadError("Directory length is truncated")
                length = DIRECTORY_LENGTH_STRUCT.unpack(length_raw)[0]
                payload = stream.read(length)
                if len(payload) != length:
                    raise ZpReadError("Directory JSON is truncated")
            parsed = parse_json_bytes(payload)
            if not isinstance(parsed, list):
                raise ZpReadError("Directory JSON must be a list")
            return [BlockDirectoryEntry(**entry) for entry in parsed]
        except ZpReadError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ZpReadError(f"Invalid directory: {exc}") from exc

    def read_block(self, block_name: str) -> Any:
        entries = [entry for entry in self.read_directory() if entry.block_name == block_name]
        if len(entries) != 1:
            raise ZpReadError(f"Expected exactly one directory entry for block {block_name!r}")
        entry = entries[0]
        if entry.encoding not in SUPPORTED_ENCODINGS:
            raise ZpReadError(f"Unsupported encoding {entry.encoding!r} for {block_name}")
        try:
            with self.file_path.open("rb") as stream:
                stream.seek(entry.offset)
                payload = stream.read(entry.length)
            if len(payload) != entry.length:
                raise ZpReadError(f"Block {block_name} is truncated")
            return parse_json_bytes(payload)
        except ZpReadError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise ZpReadError(f"Cannot read block {block_name}: {exc}") from exc

    def read_global_meta(self) -> GlobalMetaBlock:
        payload = self.read_block("global_meta")
        payload["created_at"] = parse_utc_datetime(payload["created_at"])
        return GlobalMetaBlock(**payload)

    def read_runs(self) -> list[RunBlock]:
        return [RunBlock(**item) for item in self.read_block("core_runs")]

    def read_spectra(self) -> list[SpectrumBlock]:
        return [SpectrumBlock(**item) for item in self.read_block("core_spectra")]

    def read_precursors(self) -> list[PrecursorBlock]:
        return [PrecursorBlock(**item) for item in self.read_block("core_precursors")]

    def read_chromatograms(self) -> list[ChromatogramBlock]:
        return [ChromatogramBlock(**item) for item in self.read_block("core_chromatograms")]

    def read_arrays(self) -> list[ArrayBlock]:
        return [ArrayBlock(**item) for item in self.read_block("arrays")]

    def read_indexes(self) -> IndexBlock:
        return IndexBlock(**self.read_block("indexes"))

    def read_extensions(self) -> list[ExtensionBlock]:
        return [ExtensionBlock(**item) for item in self.read_block("extensions")]

    def read_spectrum(self, spectrum_id: str) -> SpectrumBlock:
        spectrum = next((item for item in self.read_spectra() if item.spectrum_id == spectrum_id), None)
        if spectrum is None:
            raise ZpReadError(f"Unknown spectrum_id: {spectrum_id}")
        return spectrum

    def read_spectrum_arrays(self, spectrum_id: str) -> tuple[SpectrumBlock, ArrayBlock, ArrayBlock]:
        spectrum = self.read_spectrum(spectrum_id)
        arrays = {item.array_id: item for item in self.read_arrays()}
        try:
            return spectrum, arrays[spectrum.mz_array_id], arrays[spectrum.intensity_array_id]
        except KeyError as exc:
            raise ZpReadError(f"Spectrum {spectrum_id} references a missing array: {exc.args[0]}") from exc
