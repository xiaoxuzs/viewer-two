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

