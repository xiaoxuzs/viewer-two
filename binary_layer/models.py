from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .blocks import BlockCollection


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

