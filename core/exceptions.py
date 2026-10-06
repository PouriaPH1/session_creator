class SessionCreatorError(Exception):
    """Base exception for session creator project."""
    pass


class SessionNotFoundError(SessionCreatorError):
    """Raised when a requested session is not found in the pool or storage."""
    pass


class OTPNotFoundError(SessionCreatorError):
    """Raised when no fresh OTP code is found for the given session."""
    pass


class OTPTimeoutError(SessionCreatorError):
    """Raised when polling for OTP times out."""
    pass


class MasterServerError(SessionCreatorError):
    """Raised when communicating with the master OTP server fails."""
    pass


class AuthenticationFailedError(SessionCreatorError):
    """Raised when Telegram sign_in or 2FA password check fails."""
    pass


class AccountBannedError(SessionCreatorError):
    """Raised when Telegram reports the phone number is banned."""
    pass
