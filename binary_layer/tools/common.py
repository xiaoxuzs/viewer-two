from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

from ..blocks import BlockCollection, IndexBlock, StringPoolBlock
from ..exceptions import BlockValidationError, InvalidSourceError, ZpValidationError, ZpWriteError
from ..models import PipelineContext
from ..top_down_validator import combine_top_down_validation
from ..validator import ZpValidator
from ..writer import ZpWriter
from .base import BaseBlockTool, BasePipelineStep


class FileValidateStep(BasePipelineStep):
    name = "file_validate"
    category = "system"
    input_kinds = ("source_profile",)
    output_kinds = ("validated_source",)

    def run(self, context: PipelineContext) -> None:
        paths = context.source_profile.input_files
        if len(paths) != 1:
            raise InvalidSourceError(f"P0 requires exactly one input file; got {len(paths)}")
        path = paths[0]
        if not path.exists():
            raise InvalidSourceError(f"Input file does not exist: {path}")
        identity_files = context.source_profile.identity_files
        if context.source_profile.identity_files:
            if not identity_files:
                raise InvalidSourceError("Inspected bundle has no identity files")
            checked_paths = identity_files
        else:
            if not path.is_file():
                raise InvalidSourceError(f"Input path is not a regular file: {path}")
            checked_paths = (path,)
        total_size = 0
        for checked in checked_paths:
            if not checked.is_file():
                raise InvalidSourceError(f"Input path is not a regular file: {checked}")
            try:
                with checked.open("rb") as stream:
                    stream.read(1)
                total_size += checked.stat().st_size
            except OSError as exc:
                raise InvalidSourceError(f"Input file is not readable: {checked}") from exc
        context.metadata["input_file_size"] = total_size
        context.metadata["file_validated"] = True


class HashInputStep(BasePipelineStep):
    name = "hash_input"
    category = "system"
    input_kinds = ("validated_source",)
    output_kinds = ("input_sha256",)

    def run(self, context: PipelineContext) -> None:
        if context.metadata.get("file_validated") is not True:
            raise InvalidSourceError("file_validate must run before hash_input")
        digest = hashlib.sha256()
        profile = context.source_profile
        source_file_hashes: dict[str, str] = {}
        if profile.identity_files:
            for path in sorted(
                profile.identity_files,
                key=lambda item: profile.relative_label(item).encode("utf-8"),
            ):
                label_text = profile.relative_label(path)
                label = label_text.encode("utf-8")
                digest.update(struct.pack("<Q", len(label)))
                digest.update(label)
                digest.update(struct.pack("<Q", path.stat().st_size))
                item_digest = hashlib.sha256()
                _update_digests((digest, item_digest), path)
                source_file_hashes[label_text] = item_digest.hexdigest()
        else:
            _update_digest(digest, profile.input_files[0])
            source_file_hashes[profile.relative_label(profile.input_files[0])] = digest.hexdigest()
        context.metadata["input_sha256"] = digest.hexdigest()
        context.metadata["source_file_hashes"] = source_file_hashes


def _update_digest(digest: "hashlib._Hash", path: Path) -> None:  # type: ignore[name-defined]
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)


def _update_digests(digests: tuple[object, ...], path: Path) -> None:
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            for digest in digests:
                digest.update(chunk)  # type: ignore[attr-defined]


class StringPoolBuildTool(BaseBlockTool):
    name = "string_pool_build"
    input_kinds = ("core_blocks",)
    output_kinds = ("string_pool",)

    def build_blocks(self, context: PipelineContext) -> None:
        values: list[str] = []
        for run in context.blocks.runs:
            values.extend((run.source_file, run.run_name))
        values.extend(spectrum.native_id for spectrum in context.blocks.spectra)
        for chromatogram in context.blocks.chromatograms:
            values.extend((chromatogram.chromatogram_type, chromatogram.native_id))
        context.blocks.string_pool = StringPoolBlock(list(dict.fromkeys(values)))


class IndexBuildTool(BaseBlockTool):
    name = "index_build"
    input_kinds = ("core_spectra",)
    output_kinds = ("indexes",)

    def build_blocks(self, context: PipelineContext) -> None:
        spectra = context.blocks.spectra
        context.blocks.indexes = IndexBlock(
            scan_index=[
                {"scan_number": spectrum.scan_number, "spectrum_id": spectrum.spectrum_id}
                for spectrum in sorted(spectra, key=lambda item: item.scan_number)
            ],
            rt_index=[
                {"rt": spectrum.rt, "spectrum_id": spectrum.spectrum_id}
                for spectrum in sorted(spectra, key=lambda item: item.rt)
            ],
            spectrum_id_index=[
                {"spectrum_id": spectrum.spectrum_id, "position": position}
                for position, spectrum in enumerate(spectra)
            ],
        )


class ZpWriteStep(BasePipelineStep):
    name = "zp_write"
    category = "system"
    input_kinds = ("all_blocks",)
    output_kinds = ("zp_file",)

    def __init__(self, writer: ZpWriter | None = None) -> None:
        self.writer = writer or ZpWriter()

    def run(self, context: PipelineContext) -> None:
        source = context.source_profile.input_files[0]
        configured_path = context.metadata.get("output_path")
        if configured_path is not None:
            output_path = Path(str(configured_path))
        else:
            output_dir = Path(str(context.metadata.get("output_dir", source.parent)))
            output_name = str(context.metadata.get("output_name", f"{source.stem}.zp"))
            output_path = output_dir / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        format_version = context.metadata.get("format_version")
        created_at_millis = context.source_profile.output_created_at_millis
        if format_version is None:
            written = self.writer.write(
                output_path,
                context.blocks,
                created_at_millis=created_at_millis,
            )
        else:
            if type(format_version) is not int:
                raise ZpWriteError("format_version must be a plain integer")
            written = self.writer.write(
                output_path,
                context.blocks,
                format_version=format_version,
                v2_limits=context.metadata.get("v2_array_write_limits"),
                created_at_millis=created_at_millis,
            )
        context.artifacts["output_zp_path"] = written
        context.artifacts["zp_writer_metrics"] = dict(self.writer.last_metrics)
        if context.metadata.get("release_blocks_after_write") is True:
            context.blocks = BlockCollection()


class ZpValidateStep(BasePipelineStep):
    name = "zp_validate"
    category = "system"
    input_kinds = ("zp_file",)
    output_kinds = ("validation_result",)

    def __init__(self, validator: ZpValidator | None = None) -> None:
        self.validator = validator or ZpValidator()

    def run(self, context: PipelineContext) -> None:
        path = context.artifacts.get("output_zp_path")
        if not isinstance(path, (str, os.PathLike)):
            raise ZpValidationError("zp_write must run before zp_validate")
        validation_limits = context.metadata.get("v2_validation_limits")
        if validation_limits is None:
            validator = self.validator
        else:
            validator = ZpValidator()
            validator.v2_limits = validation_limits  # type: ignore[assignment]
        result = validator.validate(Path(path))
        cached_extensions = getattr(validator, "_last_v2_extensions", None)
        result = combine_top_down_validation(
            Path(path),
            result,
            extensions=cached_extensions,
        )
        from ..bottom_up_validator import combine_bottom_up_validation

        result = combine_bottom_up_validation(
            Path(path),
            result,
            extensions=cached_extensions,
        )
        validator._last_v2_extensions = None
        result.metrics.update(getattr(validator, "_last_v2_metrics", {}))
        context.artifacts["validation_result"] = result
        if not result.valid:
            codes = ", ".join(
                issue.code
                for issue in (
                    *result.issues,
                    *result.top_down_issues,
                    *result.bottom_up_issues,
                )
            )
            raise ZpValidationError(f"Generated .zp failed validation: {codes}")
