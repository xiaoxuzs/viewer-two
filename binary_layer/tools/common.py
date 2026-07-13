from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..blocks import IndexBlock, StringPoolBlock
from ..exceptions import BlockValidationError, InvalidSourceError, ZpValidationError
from ..models import PipelineContext
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
        if not path.is_file():
            raise InvalidSourceError(f"Input path is not a regular file: {path}")
        try:
            with path.open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            raise InvalidSourceError(f"Input file is not readable: {path}") from exc
        context.metadata["input_file_size"] = path.stat().st_size
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
        with context.source_profile.input_files[0].open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        context.metadata["input_sha256"] = digest.hexdigest()


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
        written = self.writer.write(output_path, context.blocks)
        context.artifacts["output_zp_path"] = written


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
        result = self.validator.validate(Path(path))
        context.artifacts["validation_result"] = result
        if not result.valid:
            codes = ", ".join(issue.code for issue in result.issues)
            raise ZpValidationError(f"Generated .zp failed validation: {codes}")

