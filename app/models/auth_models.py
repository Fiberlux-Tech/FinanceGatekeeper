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

from datetime import datetime
from enum import StrEnum
from typing import Optional

from app.models.enums import UserRole

from pydantic import BaseModel, Field


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
        AuthErrorCode.INVALID_CREDENTIALS,
        "Incorrect email or password.",
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

    model_config = {"from_attributes": True}


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
    role: Optional[UserRole] = None
    is_offline_login: bool = False

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Rate-limit models
# ---------------------------------------------------------------------------

class RateLimitState(BaseModel):
    """Per-user rate-limit counters.

    Validated model replacing raw ``dict[str, object]`` for JSON
    round-trips of rate-limit state (CLAUDE.md S2 -- Schema Validation).
    """

    failed_attempts: int = 0
    lockout_until: Optional[datetime] = None


class RateLimitStore(BaseModel):
    """Container for all per-user rate-limit entries.

    Serialized to/from ``app_settings`` as a single JSON blob keyed
    by normalized email address.
    """

    entries: dict[str, RateLimitState] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Offline session cache model
# ---------------------------------------------------------------------------

class CachedSession(BaseModel):
    """Represents a decrypted offline session payload.

    Previously lived in ``app.services.session_cache``; moved here so
    that all auth-related data models reside in the models layer.

    Attributes
    ----------
    user_id:
        The Supabase UUID of the authenticated user.
    email:
        The user's email address.
    full_name:
        The user's full name for display in UI and logs.
    role:
        The application role (e.g. ``ADMIN``, ``FINANCE``, ``SALES``).
    refresh_token:
        The Supabase refresh token used to obtain new access tokens.
    cached_at:
        ISO-8601 UTC timestamp indicating when the session was cached.
    password_hash:
        Hex-encoded PBKDF2-HMAC-SHA256 hash for offline password
        verification.  ``None`` when offline login is disabled.
    password_salt:
        Hex-encoded random 32-byte salt paired with *password_hash*.
    """

    user_id: str
    email: str
    full_name: str
    role: UserRole
    refresh_token: str
    cached_at: str  # ISO-8601 UTC
    password_hash: Optional[str] = None
    password_salt: Optional[str] = None

    model_config = {"from_attributes": True}
