from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .blocks import (
    ISOLATION_WINDOW_KIND,
    SELECTED_PRECURSOR_KIND,
)

PRECURSOR_KINDS = frozenset({SELECTED_PRECURSOR_KIND, ISOLATION_WINDOW_KIND})
PRECURSOR_RECORD_FIELDS = frozenset(
    {
        "precursor_id",
        "spectrum_id",
        "precursor_mz",
        "charge",
        "intensity",
        "precursor_kind",
        "isolation_lower_mz",
        "isolation_upper_mz",
    }
)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class PrecursorContractIssue:
    code: str
    message: str


def effective_precursor_kind(value: object) -> object:
    return SELECTED_PRECURSOR_KIND if value is None else value


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_precursor_record(
    record: Mapping[str, object],
) -> tuple[PrecursorContractIssue, ...]:
    issues: list[PrecursorContractIssue] = []
    kind = effective_precursor_kind(record.get("precursor_kind"))
    if not isinstance(kind, str) or kind not in PRECURSOR_KINDS:
        return (
            PrecursorContractIssue(
                "INVALID_PRECURSOR_KIND",
                "precursor_kind must be selected_precursor, isolation_window, or omitted for legacy selected_precursor",
            ),
        )

    if kind == SELECTED_PRECURSOR_KIND:
        charge = record.get("charge", _MISSING)
        if charge is _MISSING or charge is None:
            issues.append(
                PrecursorContractIssue(
                    "MISSING_PRECURSOR_CHARGE",
                    "selected_precursor requires a charge",
                )
            )
        elif not isinstance(charge, int) or isinstance(charge, bool) or charge <= 0:
            issues.append(
                PrecursorContractIssue(
                    "INVALID_PRECURSOR_CHARGE",
                    "selected_precursor charge must be a positive integer",
                )
            )

        precursor_mz = record.get("precursor_mz", _MISSING)
        if not _finite_number(precursor_mz) or precursor_mz < 0:
            issues.append(
                PrecursorContractIssue(
                    "INVALID_PRECURSOR_MZ",
                    "selected_precursor precursor_mz must be finite and nonnegative",
                )
            )
        intensity = record.get("intensity", _MISSING)
        if not _finite_number(intensity):
            issues.append(
                PrecursorContractIssue(
                    "INVALID_PRECURSOR_INTENSITY",
                    "selected_precursor intensity must be finite",
                )
            )
        if any(
            record.get(name) is not None
            for name in ("isolation_lower_mz", "isolation_upper_mz")
        ):
            issues.append(
                PrecursorContractIssue(
                    "PRECURSOR_KIND_FIELD_CONFLICT",
                    "selected_precursor must not carry isolation-window bounds",
                )
            )
        return tuple(issues)

    charge = record.get("charge", _MISSING)
    if charge is _MISSING or charge is not None:
        issues.append(
            PrecursorContractIssue(
                "PRECURSOR_KIND_FIELD_CONFLICT",
                "isolation_window charge must be explicitly null",
            )
        )
    if record.get("precursor_mz") is not None or record.get("intensity") is not None:
        issues.append(
            PrecursorContractIssue(
                "PRECURSOR_KIND_FIELD_CONFLICT",
                "isolation_window must not carry selected-precursor m/z or intensity",
            )
        )

    lower = record.get("isolation_lower_mz", _MISSING)
    upper = record.get("isolation_upper_mz", _MISSING)
    if lower is _MISSING or lower is None or upper is _MISSING or upper is None:
        issues.append(
            PrecursorContractIssue(
                "MISSING_ISOLATION_WINDOW",
                "isolation_window requires both isolation_lower_mz and isolation_upper_mz",
            )
        )
    elif (
        not _finite_number(lower)
        or not _finite_number(upper)
        or lower < 0
        or lower >= upper
    ):
        issues.append(
            PrecursorContractIssue(
                "INVALID_ISOLATION_WINDOW",
                "isolation-window bounds must be finite, nonnegative, and strictly increasing",
            )
        )
    return tuple(issues)
