"""Domain errors. The API layer maps them to HTTP problem responses."""


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    code = "not_found"


class Conflict(DomainError):
    code = "conflict"


class ValidationFailed(DomainError):
    code = "validation_failed"


class AuthenticationFailed(DomainError):
    code = "authentication_failed"


class PermissionDenied(DomainError):
    code = "permission_denied"


class RateLimited(DomainError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, retry_after=retry_after)
        self.retry_after = retry_after
