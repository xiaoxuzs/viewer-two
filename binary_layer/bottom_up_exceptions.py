from __future__ import annotations

from .conversion_exceptions import SourceConversionError
from .exceptions import ZpReadError


class DiaResultConversionError(SourceConversionError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.details = {} if details is None else details
        super().__init__(code, message)


class BottomUpSchemaError(ZpReadError):
    """A Bottom-Up extension set violates its versioned business schema."""
