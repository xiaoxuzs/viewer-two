class ZpError(Exception):
    """Base exception for the prototype."""


class UnsupportedSourceError(ZpError):
    pass


class InvalidSourceError(ZpError):
    pass


class PlanBuildError(ZpError):
    pass


class DuplicateStepError(ZpError):
    pass


class StepNotFoundError(ZpError):
    pass


class StepExecutionError(ZpError):
    pass


class BlockBoundaryViolationError(ZpError):
    pass


class BlockValidationError(ZpError):
    pass


class ZpWriteError(ZpError):
    pass


class ZpV2ArrayWriteError(ZpWriteError):
    def __init__(
        self,
        code: str,
        message: str,
        location: str,
        *,
        actual: object | None = None,
        limit: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.location = location
        self.actual = actual
        self.limit = limit
        details = f"{code} at {location}: {message}"
        if actual is not None:
            details += f"; actual={actual}"
        if limit is not None:
            details += f"; limit={limit}"
        super().__init__(details)


class ZpV2ResourceLimitError(ZpV2ArrayWriteError):
    pass


class ZpReadError(ZpError):
    pass


class ZpV2ArrayReadError(ZpReadError):
    def __init__(
        self,
        code: str,
        message: str,
        location: str,
        *,
        actual: object | None = None,
        limit: int | None = None,
        array_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.location = location
        self.actual = actual
        self.limit = limit
        self.array_id = array_id
        details = f"{code} at {location}: {message}"
        if array_id is not None:
            details += f"; array_id={array_id!r}"
        if actual is not None:
            details += f"; actual={actual}"
        if limit is not None:
            details += f"; limit={limit}"
        super().__init__(details)


class ZpValidationError(ZpError):
    pass


class UnsupportedVersionError(ZpReadError):
    code = "UNSUPPORTED_ZP_VERSION"

    def __init__(self, version: int, operation: str) -> None:
        self.version = version
        self.operation = operation
        super().__init__(f"Unsupported version: {version}")


class ZpVersionNotImplementedError(ZpError):
    def __init__(self, version: int, operation: str) -> None:
        self.version = version
        self.operation = operation
        self.code = f"ZP_V{version}_{operation.upper()}_NOT_IMPLEMENTED"
        super().__init__(f"ZP version {version} {operation} is not implemented")


class ChecksumMismatchError(ZpValidationError):
    pass


class MissingRequiredBlockError(ZpValidationError):
    pass


class InvalidReferenceError(ZpValidationError):
    pass


class MzmlSchemaError(ZpError):
    """A versioned mzML extension payload violates its frozen schema."""


class MzmlAdmissionError(ZpError):
    """A malformed feature profile cannot be evaluated for admission."""


class MzmlParseError(ZpError):
    """A real mzML input cannot be converted into controlled internal facts."""

    def __init__(self, code: str, message: str, location: str) -> None:
        self.code = code
        self.message = message
        self.location = location
        super().__init__(f"{code} at {location}: {message}")


class MzmlCapabilityError(MzmlParseError):
    """The input is admissible but outside the current parser stage."""
