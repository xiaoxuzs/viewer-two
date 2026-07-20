from __future__ import annotations

from .exceptions import ZpError, ZpReadError


class SourceConversionError(ZpError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ThermoRawConversionError(SourceConversionError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.details = {} if details is None else details
        super().__init__(code, message)


class TopDownConversionError(SourceConversionError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.details = {} if details is None else details
        super().__init__(code, message)


class TopDownSchemaError(ZpReadError):
    """A Top-Down extension set violates its versioned business schema."""
