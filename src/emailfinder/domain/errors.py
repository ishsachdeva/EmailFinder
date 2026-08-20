from enum import StrEnum


class ErrorCategory(StrEnum):
    CONFIG_ERROR = "CONFIG_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    NO_RESULT = "NO_RESULT"
    INVALID_RESULT = "INVALID_RESULT"
    VERIFICATION_INCONCLUSIVE = "VERIFICATION_INCONCLUSIVE"
    DATABASE_ERROR = "DATABASE_ERROR"


class EmailFinderError(RuntimeError):
    def __init__(self, category: ErrorCategory, message: str):
        self.category = category
        super().__init__(message)

