"""
Every raised error in this app is one of these, never a bare Exception.
This is what lets main.py's exception handler convert any of them into the
uniform envelope with the correct status code, in exactly one place, so no
router has to remember to catch-and-format errors itself.
"""
import logging

logger = logging.getLogger("plant_companion")


class AppException(Exception):
    """Base class. `status_code` and `error_code` mirror the OpenAPI spec's
    reusable response components (BadRequest, Unauthorized, etc.)."""

    status_code: int = 500
    error_code: str = "INTERNAL_SERVER_ERROR"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class BadRequestError(AppException):
    status_code = 400
    error_code = "INVALID_INPUT"


class UnauthorizedError(AppException):
    status_code = 401
    error_code = "UNAUTHORIZED"


class IdentityTokenInvalidError(AppException):
    status_code = 401
    error_code = "IDENTITY_TOKEN_INVALID"


class ForbiddenError(AppException):
    status_code = 403
    error_code = "FORBIDDEN"


class PlanLimitExceededError(AppException):
    status_code = 403
    error_code = "PLAN_LIMIT_EXCEEDED"


class GuestSignInRequiredError(AppException):
    """Distinct from PlanLimitExceededError — a guest hasn't demonstrated
    they'll pay for anything yet, so this should prompt sign-in, not a
    pricing screen. See build spec notes on the guest conversion flow."""
    status_code = 403
    error_code = "GUEST_SIGNIN_REQUIRED"


class NotFoundError(AppException):
    status_code = 404
    error_code = "NOT_FOUND"


class PurchaseNotFoundError(AppException):
    status_code = 404
    error_code = "PURCHASE_NOT_FOUND"


class IdentityAlreadyLinkedError(AppException):
    status_code = 409
    error_code = "IDENTITY_ALREADY_LINKED"


class PayloadTooLargeError(AppException):
    status_code = 413
    error_code = "PAYLOAD_TOO_LARGE"


class RateLimitedError(AppException):
    status_code = 429
    error_code = "RATE_LIMITED"


class InternalServerError(AppException):
    """500 — something genuinely broke unexpectedly. Every call site across
    the codebase writes `f"...: {exc}"` here for its own debugging value
    (SQL errors, stack details, ...) — that detail is logged server-side
    only (see __init__) and never reaches the client, which previously
    wasn't true: a raw asyncpg IntegrityError (full SQL, parameters,
    another user's email) once landed straight on the sign-in screen this
    way. Client-facing 4xx AppExceptions (NotFoundError, BadRequestError,
    ...) are unaffected — their messages are hand-written to be safe to
    show as-is, this only covers genuinely-unexpected failures."""
    status_code = 500
    error_code = "INTERNAL_SERVER_ERROR"

    def __init__(self, message: str) -> None:
        logger.error(message)
        super().__init__("Something went wrong on our end. Please try again.")


class ExternalProviderError(AppException):
    """502 — an external service (Gemini, Open-Meteo, Razorpay, ...) failed
    or returned something unparseable. Same reasoning as InternalServerError:
    the real provider error detail is logged server-side only."""
    status_code = 502
    error_code = "EXTERNAL_PROVIDER_ERROR"

    def __init__(self, message: str) -> None:
        logger.error(message)
        super().__init__("A service we depend on is temporarily unavailable. Please try again in a moment.")


class InvalidSignatureError(AppException):
    """Used only by the two webhook endpoints, which return 400 (not 401)
    on signature failure since there's no user session to be unauthorized
    about — matches the OpenAPI spec's note on this exact point."""
    status_code = 400
    error_code = "INVALID_SIGNATURE"


class InvalidPushTokenError(AppException):
    status_code = 400
    error_code = "INVALID_PUSH_TOKEN"
