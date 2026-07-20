from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .blocks import BlockCollection

if TYPE_CHECKING:
    from .dia_result_bundle import DiaResultBundle
    from .top_down_schema import TopDownBundle
    from .top_down_interpretation_schema import TopDownIntermediateBundle


@dataclass(frozen=True, slots=True)
class SourceProfile:
    source_type: str
    input_files: tuple[Path, ...]
    file_count: int
    has_spectra: bool
    has_chromatograms: bool
    has_identification: bool
    has_quantification: bool
    requires_pre_conversion: bool
    notes: tuple[str, ...] = ()
    path: Path | None = None
    suffix: str | None = None
    file_size: int | None = None
    run_count: int = 1
    spectrum_source_type: str | None = None
    detected_roles: tuple[str, ...] = ()
    missing_required_roles: tuple[str, ...] = ()
    ambiguous_roles: tuple[str, ...] = ()
    identity_files: tuple[Path, ...] = ()
    top_down_bundle: TopDownBundle | None = None
    top_down_intermediate_bundle: TopDownIntermediateBundle | None = None
    dia_result_bundle: DiaResultBundle | None = None
    output_created_at_millis: int | None = None

    def relative_label(self, path: Path) -> str:
        bundle = (
            self.dia_result_bundle
            or self.top_down_bundle
            or self.top_down_intermediate_bundle
        )
        return bundle.relative_label(path) if bundle is not None else path.name


@dataclass(frozen=True, slots=True)
class SourceFileIdentity:
    file_size: int
    sha256: str
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ConversionOptions:
    converter_path: Path | None = None
    temporary_directory: Path | None = None
    keep_intermediate: bool = False
    timeout_seconds: float = 3600.0
    requested_conversion_kind: str | None = None
    top_down_interpreter_script: Path | None = None
    python_executable: Path | None = None
    keep_generated_interpretation: bool = False
    interpretation_timeout_seconds: float = 3600.0
    generated_interpretation_directory: Path | None = None

    def __post_init__(self) -> None:
        if self.converter_path is not None and not isinstance(self.converter_path, Path):
            raise TypeError("converter_path must be a pathlib.Path or None")
        if self.temporary_directory is not None and not isinstance(self.temporary_directory, Path):
            raise TypeError("temporary_directory must be a pathlib.Path or None")
        if type(self.keep_intermediate) is not bool:
            raise TypeError("keep_intermediate must be a boolean")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be a positive number")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.requested_conversion_kind not in {None, "top_down"}:
            raise ValueError("requested_conversion_kind must be None or 'top_down'")
        for field_name, value in (
            ("top_down_interpreter_script", self.top_down_interpreter_script),
            ("python_executable", self.python_executable),
            ("generated_interpretation_directory", self.generated_interpretation_directory),
        ):
            if value is not None and not isinstance(value, Path):
                raise TypeError(f"{field_name} must be a pathlib.Path or None")
        if type(self.keep_generated_interpretation) is not bool:
            raise TypeError("keep_generated_interpretation must be a boolean")
        if isinstance(self.interpretation_timeout_seconds, bool) or not isinstance(
            self.interpretation_timeout_seconds, (int, float)
        ):
            raise TypeError("interpretation_timeout_seconds must be a positive number")
        if self.interpretation_timeout_seconds <= 0:
            raise ValueError("interpretation_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ConversionPlan:
    plan_id: str
    source_type: str
    required_steps: tuple[str, ...]
    output_extension: str
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class PipelineLogEntry:
    step_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    message: str = ""


@dataclass(slots=True)
class PipelineContext:
    source_profile: SourceProfile
    metadata: dict[str, object] = field(default_factory=dict)
    blocks: BlockCollection = field(default_factory=BlockCollection)
    artifacts: dict[str, object] = field(default_factory=dict)
    logs: list[PipelineLogEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ZpHeader:
    magic: bytes
    version: int
    endianness: int
    flags: int
    created_at: int
    directory_offset: int


@dataclass(frozen=True, slots=True)
class BlockDirectoryEntry:
    block_name: str
    offset: int
    length: int
    encoding: str
    checksum: str


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    block_name: str | None = None


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue]
    checked_blocks: int
    file_path: Path
    version: int | None
    top_down_valid: bool | None = None
    top_down_issues: list[ValidationIssue] = field(default_factory=list)
    bottom_up_valid: bool | None = None
    bottom_up_issues: list[ValidationIssue] = field(default_factory=list)
    mode: str = "deep"
    file_sha256: str | None = None
    certificate_valid: bool | None = None
    deep_validation_reused: bool = False
    certificate_summary: dict[str, object] = field(default_factory=dict)
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversionResult:
    source_path: Path
    target_path: Path
    source_profile: SourceProfile
    plan: ConversionPlan
    format_version: int
    validation: ValidationResult
    source_before: SourceFileIdentity
    source_after: SourceFileIdentity
    output_file_size: int
    output_sha256: str
    converter_path: Path | None = None
    converter_name: str | None = None
    converter_version: str | None = None
    converter_exit_code: int | None = None
    converter_command: tuple[str, ...] = ()
    converter_stdout: str = ""
    converter_stderr: str = ""
    intermediate_path: Path | None = None
    intermediate_file_size: int | None = None
    intermediate_sha256: str | None = None
    cleanup_result: str = "not_applicable"
    performance: dict[str, int | float | str] = field(default_factory=dict)
