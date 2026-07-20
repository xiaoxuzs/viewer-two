from pathlib import Path
import re
from typing import Iterable

from .exceptions import InvalidSourceError
from .conversion_exceptions import TopDownConversionError
from .bottom_up_exceptions import DiaResultConversionError
from .dia_result_bundle import DiaResultBundleInspector
from .models import SourceProfile
from .top_down_adapter import TopDownAdapter
from .top_down_interpretation_adapter import (
    TopDownInterpretationAdapter,
    mzml_alone_error,
)


class SourceInspector:
    def __init__(
        self,
        top_down_adapter: TopDownAdapter | None = None,
        top_down_interpretation_adapter: TopDownInterpretationAdapter | None = None,
        dia_result_bundle_inspector: DiaResultBundleInspector | None = None,
    ) -> None:
        self.top_down_adapter = top_down_adapter or TopDownAdapter()
        self.top_down_interpretation_adapter = (
            top_down_interpretation_adapter or TopDownInterpretationAdapter()
        )
        self.dia_result_bundle_inspector = (
            dia_result_bundle_inspector or DiaResultBundleInspector()
        )

    def inspect(
        self,
        input_files: Iterable[str | Path],
        *,
        requested_conversion_kind: str | None = None,
    ) -> SourceProfile:
        paths = tuple(Path(item) for item in input_files)
        if len(paths) != 1:
            raise InvalidSourceError(f"P0 requires exactly one input file; got {len(paths)}")

        path = paths[0]
        if path.is_dir():
            try:
                dia_bundle = self.dia_result_bundle_inspector.inspect_bundle(path)
            except DiaResultConversionError as exc:
                if exc.code != "MISSING_DIANN_REPORT":
                    raise
            else:
                return SourceProfile(
                    source_type="real_dia_result_bundle",
                    input_files=paths,
                    file_count=len(dia_bundle.source_files),
                    has_spectra=True,
                    has_chromatograms=True,
                    has_identification=True,
                    has_quantification=True,
                    requires_pre_conversion=False,
                    notes=(
                        "Single-run Thermo DIA mzML plus DIA-NN 2.0 Parquet bundle inspected by exact Run matching.",
                    ),
                    path=path,
                    file_size=sum(item.path.stat().st_size for item in dia_bundle.source_files),
                    run_count=1,
                    spectrum_source_type="mzml",
                    detected_roles=dia_bundle.detected_roles,
                    missing_required_roles=(),
                    ambiguous_roles=(),
                    identity_files=dia_bundle.identity_files,
                    dia_result_bundle=dia_bundle,
                    output_created_at_millis=dia_bundle.output_created_at_millis,
                )
        if path.is_dir() or (path.is_file() and path.suffix.lower() == ".json"):
            deferred_top_down_error: TopDownConversionError | None = None
            try:
                bundle = self.top_down_adapter.inspect_bundle(path)
            except TopDownConversionError as exc:
                if exc.code != "TOP_DOWN_BUNDLE_NOT_FOUND":
                    if path.is_dir() and not _has_precomputed_prsm(path):
                        deferred_top_down_error = exc
                    else:
                        raise
            else:
                return SourceProfile(
                source_type="real_top_down_bundle",
                input_files=paths,
                file_count=len(bundle.source_files),
                has_spectra=True,
                has_chromatograms=False,
                has_identification=True,
                has_quantification="feature_result" in bundle.detected_roles,
                requires_pre_conversion=bundle.spectrum_source_type == "thermo_raw",
                notes=(
                    "Viewer-compatible single-run Top-Down bundle inspected by content and role.",
                ),
                path=path,
                suffix=path.suffix if path.is_file() else None,
                file_size=sum(item.stat().st_size for item in bundle.source_files),
                run_count=1,
                spectrum_source_type=bundle.spectrum_source_type,
                detected_roles=bundle.detected_roles,
                missing_required_roles=(),
                ambiguous_roles=(),
                identity_files=bundle.source_files,
                top_down_bundle=bundle,
            )
            if path.is_dir():
                try:
                    intermediate = self.top_down_interpretation_adapter.inspect_bundle(path)
                except TopDownConversionError as exc:
                    if exc.code != "TOP_DOWN_INTERMEDIATE_BUNDLE_NOT_FOUND":
                        raise
                    if deferred_top_down_error is not None:
                        raise deferred_top_down_error
                    if requested_conversion_kind == "top_down" and _has_mzml_at_depth_one(path):
                        raise mzml_alone_error()
                else:
                    return SourceProfile(
                        source_type="real_top_down_intermediate_bundle",
                        input_files=paths,
                        file_count=len(intermediate.source_files),
                        has_spectra=True,
                        has_chromatograms=False,
                        has_identification=True,
                        has_quantification=False,
                        requires_pre_conversion=True,
                        notes=(
                            "Single-run TopPIC/TopFD intermediate bundle inspected by content and references.",
                        ),
                        path=path,
                        file_size=sum(item.stat().st_size for item in intermediate.source_files),
                        run_count=1,
                        spectrum_source_type="mzml",
                        detected_roles=intermediate.detected_roles,
                        missing_required_roles=(),
                        ambiguous_roles=(),
                        identity_files=intermediate.source_files,
                        top_down_intermediate_bundle=intermediate,
                    )
        suffix = path.suffix.lower()
        if suffix == ".mzml" and requested_conversion_kind == "top_down":
            raise mzml_alone_error()
        source_type = {".mzml": "real_mzml", ".raw": "real_thermo_raw"}.get(suffix, "unknown")
        known = source_type != "unknown"
        try:
            file_size = path.stat().st_size if path.is_file() else None
        except OSError:
            file_size = None
        return SourceProfile(
            source_type=source_type,
            input_files=paths,
            file_count=1,
            has_spectra=known,
            has_chromatograms=False,
            has_identification=False,
            has_quantification=False,
            requires_pre_conversion=source_type == "real_thermo_raw",
            notes=("Extension-only inspection; file content is not parsed.",),
            path=path,
            suffix=path.suffix,
            file_size=file_size,
        )


def _has_mzml_at_depth_one(root: Path) -> bool:
    try:
        children = tuple(root.iterdir())
        return any(item.is_file() and item.suffix.lower() == ".mzml" for item in children) or any(
            item.is_file() and item.suffix.lower() == ".mzml"
            for directory in children
            if directory.is_dir()
            for item in directory.iterdir()
        )
    except OSError:
        return False


def _has_precomputed_prsm(root: Path) -> bool:
    try:
        return any(
            path.is_file()
            and path.suffix.lower() in {".js", ".json", ".txt"}
            and re.fullmatch(r"prsm\d+", path.stem, re.IGNORECASE) is not None
            for path in root.rglob("*")
        )
    except OSError:
        return False
