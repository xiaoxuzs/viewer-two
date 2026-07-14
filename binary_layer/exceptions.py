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


class ZpReadError(ZpError):
    pass


class ZpValidationError(ZpError):
    pass


class UnsupportedVersionError(ZpReadError):
    pass


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
