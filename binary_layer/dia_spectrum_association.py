from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from .blocks import BlockCollection, ISOLATION_WINDOW_KIND
from .bottom_up_exceptions import DiaResultConversionError

ASSOCIATION_METHOD = "rt_and_isolation_window_nearest_ms2"
ASSOCIATION_KIND = "derived_nearest_dia_window"
ASSOCIATION_VERSION = 1
ASSOCIATION_RT_UNIT = "minute"
ASSOCIATION_MAX_DELTA_MINUTES = 0.5
ASSOCIATION_WINDOW_RULE = "closed_absolute_bounds_no_mz_tolerance"
ASSOCIATION_TIE_BREAK_RULE = "minimum_absolute_rt_delta_then_scan_number"


@dataclass(frozen=True, slots=True)
class DiaSpectrumAssociation:
    spectrum_id: str
    scan_number: int
    rt_seconds: float
    rt_delta_seconds: float


@dataclass(frozen=True, slots=True)
class _WindowGroup:
    lower_mz: float
    upper_mz: float
    rt_seconds: tuple[float, ...]
    rows: tuple[tuple[int, str, float], ...]


class DiaSpectrumAssociator:
    """Deterministic Viewer-compatible RT plus DIA-window association."""

    def __init__(
        self,
        blocks: BlockCollection,
        *,
        max_delta_minutes: float = ASSOCIATION_MAX_DELTA_MINUTES,
    ) -> None:
        if (
            isinstance(max_delta_minutes, bool)
            or not isinstance(max_delta_minutes, (int, float))
            or not math.isfinite(max_delta_minutes)
            or max_delta_minutes < 0
        ):
            raise ValueError("max_delta_minutes must be finite and non-negative")
        precursors = {item.precursor_id: item for item in blocks.precursors}
        grouped: dict[tuple[float, float], list[tuple[int, str, float]]] = {}
        for spectrum in blocks.spectra:
            if spectrum.ms_level != 2 or spectrum.precursor_id is None:
                continue
            precursor = precursors.get(spectrum.precursor_id)
            if (
                precursor is None
                or precursor.effective_precursor_kind != ISOLATION_WINDOW_KIND
                or precursor.isolation_lower_mz is None
                or precursor.isolation_upper_mz is None
            ):
                raise DiaResultConversionError(
                    "DIA_WINDOW_MALFORMED",
                    f"MS2 {spectrum.spectrum_id} lacks one isolation-window core precursor",
                )
            key = (precursor.isolation_lower_mz, precursor.isolation_upper_mz)
            grouped.setdefault(key, []).append(
                (spectrum.scan_number, spectrum.spectrum_id, spectrum.rt)
            )
        self._groups = tuple(
            _WindowGroup(
                lower,
                upper,
                tuple(row[2] for row in ordered),
                tuple(ordered),
            )
            for (lower, upper), rows in sorted(grouped.items())
            for ordered in [sorted(rows, key=lambda item: (item[2], item[0]))]
        )
        self.max_delta_seconds = float(max_delta_minutes) * 60.0

    def associate(self, rt_minutes: float, precursor_mz: float) -> DiaSpectrumAssociation:
        for value, label in ((rt_minutes, "RT"), (precursor_mz, "Precursor.Mz")):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise DiaResultConversionError(
                    "DIANN_ROW_MALFORMED",
                    f"{label} must be a finite non-negative number",
                )
        target_rt = float(rt_minutes) * 60.0
        candidates: list[tuple[float, int, str, float]] = []
        for group in self._groups:
            if not group.lower_mz <= precursor_mz <= group.upper_mz:
                continue
            position = bisect.bisect_left(group.rt_seconds, target_rt)
            for candidate_position in (position - 1, position):
                if not 0 <= candidate_position < len(group.rows):
                    continue
                scan_number, spectrum_id, rt_seconds = group.rows[candidate_position]
                candidates.append(
                    (abs(rt_seconds - target_rt), scan_number, spectrum_id, rt_seconds)
                )
        if not candidates:
            raise DiaResultConversionError(
                "IDENTIFICATION_SPECTRUM_NOT_FOUND",
                "No DIA MS2 isolation window contains the identification precursor m/z",
            )
        delta, scan_number, spectrum_id, rt_seconds = min(candidates)
        if delta > self.max_delta_seconds:
            raise DiaResultConversionError(
                "IDENTIFICATION_SPECTRUM_NOT_FOUND",
                "Nearest DIA MS2 exceeds the frozen 0.5 minute RT tolerance",
                details={"rt_delta_seconds": delta},
            )
        return DiaSpectrumAssociation(
            spectrum_id=spectrum_id,
            scan_number=scan_number,
            rt_seconds=rt_seconds,
            rt_delta_seconds=delta,
        )

    @property
    def provenance(self) -> dict[str, object]:
        return {
            "association_method": ASSOCIATION_METHOD,
            "association_version": ASSOCIATION_VERSION,
            "association_kind": ASSOCIATION_KIND,
            "rt_unit": ASSOCIATION_RT_UNIT,
            "max_delta_minutes": ASSOCIATION_MAX_DELTA_MINUTES,
            "window_rule": ASSOCIATION_WINDOW_RULE,
            "tie_break_rule": ASSOCIATION_TIE_BREAK_RULE,
        }
