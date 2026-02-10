"""
Authentication Pipeline Models.

Pydantic models and enumerations for the auth request/response
contracts between ``AuthService`` and the UI layer.

These models enforce the typed boundary described in CLAUDE.md
(Section 2 — Pythonic Type Safety) and ensure that every auth
operation returns a structured, inspectable result rather than
raw strings or exception side-channels.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class AuthErrorCode(StrEnum):
    """Exhaustive enumeration of authentication error categories.

    Used by ``AuthService`` to classify Supabase errors and by the
    UI layer to decide which feedback to display.
    """

    INVALID_CREDENTIALS = "invalid_credentials"
    USER_NOT_FOUND = "user_not_found"
    USER_BANNED = "user_banned"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    NETWORK_ERROR = "network_error"
    TIMEOUT_ERROR = "timeout_error"
    RATE_LIMITED = "rate_limited"
    VALIDATION_ERROR = "validation_error"
    SESSION_EXPIRED = "session_expired"
    UNKNOWN_ERROR = "unknown_error"


# ---------------------------------------------------------------------------
# Supabase error-code mapping
# ---------------------------------------------------------------------------

SUPABASE_ERROR_MAP: dict[str, tuple[AuthErrorCode, str]] = {
    "invalid_credentials": (
        AuthErrorCode.INVALID_CREDENTIALS,
        "Incorrect email or password.",
    ),
    "invalid_grant": (
        AuthErrorCode.INVALID_CREDENTIALS,
        "Incorrect email or password.",
    ),
    "user_not_found": (
        AuthErrorCode.USER_NOT_FOUND,
        "No account found for this email.",
    ),
    "user_banned": (
        AuthErrorCode.USER_BANNED,
        "Your account has been deactivated. Contact your administrator.",
    ),
    "user_already_exists": (
        AuthErrorCode.EMAIL_ALREADY_EXISTS,
        "An account with this email already exists. Try signing in.",
    ),
}


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

class ValidationResult(BaseModel):
    """Result of a single client-side field validation check.

    Attributes
    ----------
    is_valid:
        ``True`` when the value passes the validation rule.
    error_message:
        Human-readable description of the failure, or ``None`` on success.
    """

    is_valid: bool
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Unified auth response
# ---------------------------------------------------------------------------

class AuthResult(BaseModel):
    """Unified response for login, registration, and password-reset
    operations.

    The UI layer inspects ``success`` to decide the happy-path vs.
    error-path rendering, and uses ``error_code`` to conditionally
    show extra controls.

    Attributes
    ----------
    success:
        ``True`` when the operation completed without error.
    error_code:
        Structured error category (``None`` on success).
    error_message:
        Human-readable error description (``None`` on success).
    user_id:
        The Supabase UUID of the authenticated / registered user.
    email:
        The user's normalised email address.
    full_name:
        The user's display name (first + last).
    role:
        Application role assigned to the user.
    is_offline_login:
        ``True`` when the login succeeded via the encrypted offline
        cache rather than a live Supabase round-trip.
    """

    success: bool
    error_code: Optional[AuthErrorCode] = None
    error_message: Optional[str] = None
    user_id: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[Literal["SALES", "FINANCE", "ADMIN"]] = None
    is_offline_login: bool = False
