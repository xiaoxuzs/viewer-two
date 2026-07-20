from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from .bottom_up_schema import DIANN_COLUMN_NAMES
from .bottom_up_exceptions import DiaResultConversionError

SOURCE_TYPE = "real_dia_result_bundle"
ADAPTER_FLAVOR = "diann_2_parquet"


def normalize_run_name(value: str) -> str:
    name = (value or "").strip().replace("\\", "/").split("/")[-1]
    folded = name.casefold()
    for suffix in (".mzml.gz", ".mzml", ".raw", ".d"):
        if folded.endswith(suffix):
            return folded[: -len(suffix)]
    return folded


@dataclass(frozen=True, slots=True)
class DiaSourceFile:
    path: Path
    role: str
    processing_status: str


@dataclass(frozen=True, slots=True)
class DiaResultBundle:
    input_path: Path
    root: Path
    primary_report: Path
    report_role: str
    optional_report: Path | None
    spectrum_source: Path
    report_run_name: str
    normalized_run_name: str
    report_columns: tuple[str, ...]
    report_row_count: int
    source_files: tuple[DiaSourceFile, ...]
    output_created_at_millis: int

    @property
    def identity_files(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.source_files)

    @property
    def detected_roles(self) -> tuple[str, ...]:
        return tuple(sorted({item.role for item in self.source_files}))

    def relative_label(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.name

    def role_for(self, path: Path) -> str:
        resolved = path.resolve()
        for item in self.source_files:
            if item.path.resolve() == resolved:
                return item.role
        raise KeyError(path)


class DiaResultBundleInspector:
    def inspect_bundle(self, source: str | Path) -> DiaResultBundle:
        root = Path(source).resolve()
        if not root.is_dir():
            raise DiaResultConversionError(
                "MISSING_DIANN_REPORT",
                "A DIA-NN result bundle must be a directory",
            )
        files = tuple(path for path in root.rglob("*") if path.is_file())
        all_reports = tuple(
            path for path in files if path.name.casefold() == "all_report.parquet"
        )
        target_reports = tuple(
            path for path in files if path.name.casefold() == "target_report.parquet"
        )
        if len(all_reports) > 1 or len(target_reports) > 1:
            raise DiaResultConversionError(
                "AMBIGUOUS_DIANN_REPORT",
                "A DIA-NN report role has multiple candidates",
            )
        if all_reports:
            primary = all_reports[0]
            role = "all_report"
            optional = target_reports[0] if target_reports else None
        elif target_reports:
            primary = target_reports[0]
            role = "target_report"
            optional = None
        else:
            raise DiaResultConversionError(
                "MISSING_DIANN_REPORT",
                "No all_report.parquet or target_report.parquet was found",
            )

        try:
            parquet = pq.ParquetFile(primary)
        except Exception as exc:
            raise DiaResultConversionError(
                "DIANN_REPORT_READ_FAILED",
                f"DIA-NN report metadata cannot be read: {exc}",
            ) from exc
        columns = tuple(parquet.schema_arrow.names)
        if "Run" not in columns:
            raise DiaResultConversionError(
                "DIANN_REQUIRED_COLUMN_MISSING",
                "DIA-NN report is missing required column Run",
            )
        run_names: set[str] = set()
        for batch in parquet.iter_batches(columns=["Run"], batch_size=65_536):
            for value in batch.column(0).to_pylist():
                if isinstance(value, str) and value.strip():
                    run_names.add(value.strip())
        if not run_names:
            raise DiaResultConversionError(
                "DIANN_ROW_MALFORMED",
                "DIA-NN report has no non-empty Run value",
            )
        if len(run_names) != 1:
            raise DiaResultConversionError(
                "MULTI_RUN_BUNDLE_NOT_SUPPORTED",
                "One .zp target cannot contain multiple DIA-NN runs",
                details={"run_count": len(run_names)},
            )
        report_run_name = next(iter(run_names))
        normalized = normalize_run_name(report_run_name)
        mzml_files = tuple(
            path for path in files if path.suffix.casefold() == ".mzml"
        )
        matching = tuple(
            path for path in mzml_files if normalize_run_name(path.name) == normalized
        )
        if not matching:
            raise DiaResultConversionError(
                "DIANN_RUN_NOT_MATCHED" if mzml_files else "MISSING_SPECTRUM_SOURCE",
                "DIA-NN Run does not exactly match an mzML source",
            )
        if len(matching) != 1 or len(mzml_files) != 1:
            raise DiaResultConversionError(
                "AMBIGUOUS_SPECTRUM_SOURCE",
                "The bundle must contain exactly one uniquely matched mzML source",
            )
        spectrum_source = matching[0]

        source_files = [
            DiaSourceFile(primary, "primary_report", "typed_and_preserved"),
            DiaSourceFile(spectrum_source, "spectrum_source", "typed"),
        ]
        if optional is not None:
            source_files.append(
                DiaSourceFile(optional, "refined_report", "preserved_not_imported")
            )
        selected = {primary.resolve(), spectrum_source.resolve()}
        if optional is not None:
            selected.add(optional.resolve())
        for path in files:
            if path.resolve() in selected:
                continue
            classified = _classify_optional(path, primary.parent)
            if classified is not None:
                source_files.append(classified)
        source_files.sort(key=lambda item: self._relative(root, item.path).encode("utf-8"))
        return DiaResultBundle(
            input_path=root,
            root=root,
            primary_report=primary,
            report_role=role,
            optional_report=optional,
            spectrum_source=spectrum_source,
            report_run_name=report_run_name,
            normalized_run_name=normalized,
            report_columns=columns,
            report_row_count=parquet.metadata.num_rows,
            source_files=tuple(source_files),
            output_created_at_millis=_mzml_start_millis(spectrum_source),
        )

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return path.name


def _classify_optional(path: Path, report_directory: Path) -> DiaSourceFile | None:
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    in_report_directory = path.parent.resolve() == report_directory.resolve()
    if suffix in {".fasta", ".fa", ".faa"}:
        return DiaSourceFile(path, "fasta", "preserved_not_loaded")
    if not in_report_directory:
        return None
    if name.endswith("_lib.parquet"):
        return DiaSourceFile(path, "spectral_library", "preserved_not_loaded")
    if name.endswith(".stats.tsv"):
        return DiaSourceFile(path, "stats", "preserved_not_loaded")
    if name.endswith(".protein_description.tsv"):
        return DiaSourceFile(path, "protein_description", "preserved_not_loaded")
    if name.endswith("_matrix.tsv"):
        return DiaSourceFile(path, "quant_matrix", "preserved_not_loaded")
    if name.endswith(".log.txt"):
        return DiaSourceFile(path, "log", "metadata_evidence_only")
    if name.endswith(".manifest.txt"):
        return DiaSourceFile(path, "manifest", "metadata_evidence_only")
    if name.endswith(".pos.pkl"):
        return DiaSourceFile(path, "pfmb_pickle", "unsafe_preserved_not_loaded")
    if name.endswith(".infoneg.pkl"):
        return DiaSourceFile(path, "infoneg_pickle", "unsafe_preserved_not_loaded")
    return None


def _mzml_start_millis(path: Path) -> int:
    try:
        for event, element in ET.iterparse(path, events=("start",)):
            if element.tag.rsplit("}", 1)[-1] != "run":
                continue
            value = element.attrib.get("startTimeStamp")
            if not value:
                return 0
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int(parsed.timestamp() * 1000))
    except (OSError, ET.ParseError, ValueError, OverflowError):
        return 0
    return 0


def required_report_columns() -> tuple[str, ...]:
    required = {
        "Run",
        "Precursor.Id",
        "Modified.Sequence",
        "Stripped.Sequence",
        "Precursor.Charge",
        "Decoy",
        "Precursor.Mz",
        "RT",
        "RT.Start",
        "RT.Stop",
        "Q.Value",
    }
    return tuple(name for name in DIANN_COLUMN_NAMES if name in required)


_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


def is_absolute_source_value(value: str) -> bool:
    return _ABSOLUTE_PATH.match(value) is not None
