"""
Authentication Service.

Single orchestrator for every authentication concern in the
FinanceGatekeeper pipeline: login, registration, logout, password
reset, rate limiting, and error classification.

Sits between the UI layer and the Supabase / session-cache layer so
that ``LoginView`` remains a thin form handler (CLAUDE.md §2 — "Thin
UI" Rule).

All methods return typed ``AuthResult`` or ``ValidationResult``
models — the UI never inspects raw exceptions.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.auth import CurrentUser, SessionManager
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.auth_models import (
    AuthErrorCode,
    AuthResult,
    SUPABASE_ERROR_MAP,
    ValidationResult,
)
from app.services.jit_provisioning import JITProvisioningService
from app.services.session_cache import SessionCacheService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMAIL_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

_MAX_FAILED_ATTEMPTS: int = 3
_LOCKOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AuthService:
    """Centralised authentication service.

    Receives all infrastructure dependencies via ``__init__`` and
    exposes pure request → result methods for every auth flow.

    Parameters
    ----------
    db:
        Initialised database manager (Supabase + SQLite).
    session:
        Injectable session holder for the authenticated user.
    jit_service:
        Just-in-time provisioning service for local user sync.
    session_cache:
        Encrypted offline session cache service.
    logger:
        Structured JSON logger for audit-grade logging.
    """

    def __init__(
        self,
        db: DatabaseManager,
        session: SessionManager,
        jit_service: JITProvisioningService,
        session_cache: SessionCacheService,
        logger: StructuredLogger,
    ) -> None:
        self._db: DatabaseManager = db
        self._session: SessionManager = session
        self._jit_service: JITProvisioningService = jit_service
        self._session_cache: SessionCacheService = session_cache
        self._logger: StructuredLogger = logger

        # Rate-limit state (in-memory — resets on app restart)
        self._failed_attempts: int = 0
        self._lockout_until: Optional[datetime] = None

    # ==================================================================
    # Validation helpers
    # ==================================================================

    @staticmethod
    def validate_email(email: str) -> ValidationResult:
        """Validate an email address against a simplified RFC 5322 regex.

        Parameters
        ----------
        email:
            The raw email string to validate.

        Returns
        -------
        ValidationResult
            ``is_valid=True`` if the email matches, otherwise a
            human-readable ``error_message``.
        """
        if not email or not email.strip():
            return ValidationResult(
                is_valid=False,
                error_message="Email address is required.",
            )
        if not _EMAIL_RE.match(email.strip()):
            return ValidationResult(
                is_valid=False,
                error_message="Please enter a valid email address.",
            )
        return ValidationResult(is_valid=True)

    @staticmethod
    def validate_password(password: str) -> ValidationResult:
        """Enforce the password policy.

        Policy: minimum 8 characters, at least 1 uppercase letter,
        1 lowercase letter, 1 digit, and 1 special character.

        Parameters
        ----------
        password:
            The raw password string to validate.

        Returns
        -------
        ValidationResult
        """
        if len(password) < 8:
            return ValidationResult(
                is_valid=False,
                error_message="Password must be at least 8 characters.",
            )
        if not re.search(r"[A-Z]", password):
            return ValidationResult(
                is_valid=False,
                error_message="Password must contain at least one uppercase letter.",
            )
        if not re.search(r"[a-z]", password):
            return ValidationResult(
                is_valid=False,
                error_message="Password must contain at least one lowercase letter.",
            )
        if not re.search(r"\d", password):
            return ValidationResult(
                is_valid=False,
                error_message="Password must contain at least one digit.",
            )
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\;'/`~]", password):
            return ValidationResult(
                is_valid=False,
                error_message="Password must contain at least one special character.",
            )
        return ValidationResult(is_valid=True)

    @staticmethod
    def validate_name(name: str, field_label: str) -> ValidationResult:
        """Validate a name field (first name or last name).

        Parameters
        ----------
        name:
            The raw name string.
        field_label:
            Human label for the error message (e.g. ``"First name"``).

        Returns
        -------
        ValidationResult
        """
        stripped = name.strip()
        if not stripped:
            return ValidationResult(
                is_valid=False,
                error_message=f"{field_label} is required.",
            )
        if len(stripped) < 2:
            return ValidationResult(
                is_valid=False,
                error_message=f"{field_label} must be at least 2 characters.",
            )
        return ValidationResult(is_valid=True)

    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalise an email address: strip whitespace and lowercase."""
        return email.strip().lower()

    # ==================================================================
    # Rate limiting
    # ==================================================================

    def check_rate_limit(self) -> tuple[bool, int]:
        """Check whether the user is currently rate-limited.

        Returns
        -------
        tuple[bool, int]
            ``(is_locked, remaining_seconds)``.  When ``is_locked`` is
            ``False``, ``remaining_seconds`` is ``0``.
        """
        if self._lockout_until is None:
            return False, 0

        now = datetime.now(tz=timezone.utc)
        if now >= self._lockout_until:
            self._reset_rate_limit()
            return False, 0

        remaining = int((self._lockout_until - now).total_seconds()) + 1
        return True, remaining

    def _record_failed_attempt(self) -> None:
        """Record a failed login attempt and engage the lockout if the
        threshold is reached."""
        self._failed_attempts += 1
        if self._failed_attempts >= _MAX_FAILED_ATTEMPTS:
            self._lockout_until = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=_LOCKOUT_SECONDS)
            )
            self._logger.warning(
                "Rate limit engaged: %d failed attempts. Locked for %ds.",
                self._failed_attempts,
                _LOCKOUT_SECONDS,
            )

    def _reset_rate_limit(self) -> None:
        """Clear the rate-limit counters after a successful login or
        after the lockout period expires."""
        self._failed_attempts = 0
        self._lockout_until = None

    # ==================================================================
    # Login
    # ==================================================================

    def login(self, email: str, password: str) -> AuthResult:
        """Authenticate a user via Supabase, falling back to offline
        cache when the server is unreachable.

        Covers roadmap items 1.5.2 (error mapping, rate limit, email
        normalisation) and 1.5.3 (offline password verification).

        Parameters
        ----------
        email:
            The raw email entered by the user.
        password:
            The raw password entered by the user.

        Returns
        -------
        AuthResult
            ``success=True`` on authentication, or a structured error
            with ``error_code`` and ``error_message`` on failure.
        """
        email = self.normalize_email(email)

        # --- Rate-limit gate ---
        is_locked, remaining = self.check_rate_limit()
        if is_locked:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.RATE_LIMITED,
                error_message=(
                    f"Too many failed attempts. Please wait {remaining} seconds."
                ),
            )

        # --- Online authentication ---
        try:
            response = self._db.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
            user_data = response.user
            session_data = response.session
            user_metadata = user_data.user_metadata or {}

            current_user = CurrentUser(
                id=user_data.id,
                email=user_data.email or email,
                full_name=user_metadata.get("full_name", email.split("@")[0]),
                role=user_metadata.get("role", "SALES"),
            )

            # Session + tokens
            self._session.set_current_user(current_user)
            self._session.set_tokens(
                access_token=session_data.access_token,
                refresh_token=session_data.refresh_token,
                expires_at=session_data.expires_at,
            )

            # JIT provisioning
            self._jit_service.ensure_user_synced(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
            )

            # Hash password for offline verification
            pw_hash, pw_salt = SessionCacheService.hash_password(password)

            # Cache session with password hash
            self._session_cache.cache_session(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
                refresh_token=session_data.refresh_token,
                password_hash=pw_hash,
                password_salt=pw_salt,
            )

            self._reset_rate_limit()

            self._logger.info(
                "User authenticated: %s (role: %s)",
                current_user.full_name,
                current_user.role,
                extra={
                    "event": "LOGIN",
                    "email": current_user.email,
                    "user_id": current_user.id,
                },
            )

            return AuthResult(
                success=True,
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
            )

        except RuntimeError:
            # Supabase unavailable — try offline fallback
            return self._offline_login(email, password)

        except Exception as exc:
            return self._classify_login_error(exc)

    def _offline_login(self, email: str, password: str) -> AuthResult:
        """Attempt offline login with password verification.

        Parameters
        ----------
        email:
            Normalised email address.
        password:
            Plaintext password entered by the user.

        Returns
        -------
        AuthResult
        """
        cached = self._session_cache.verify_offline_password(email, password)

        if cached is None:
            # Either no cache, email mismatch, no hash, or wrong password
            cached_any = self._session_cache.load_cached_session()
            if cached_any is None:
                return AuthResult(
                    success=False,
                    error_code=AuthErrorCode.NETWORK_ERROR,
                    error_message=(
                        "No internet connection. "
                        "Sign in online first to enable offline access."
                    ),
                )
            if cached_any.email != email:
                return AuthResult(
                    success=False,
                    error_code=AuthErrorCode.NETWORK_ERROR,
                    error_message=(
                        "No internet connection. "
                        "Sign in online first to enable offline access."
                    ),
                )
            # Email matched but password wrong (or no hash stored)
            if cached_any.password_hash is None:
                return AuthResult(
                    success=False,
                    error_code=AuthErrorCode.NETWORK_ERROR,
                    error_message=(
                        "No internet connection. "
                        "Sign in online to update your offline credentials."
                    ),
                )
            self._record_failed_attempt()
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.INVALID_CREDENTIALS,
                error_message="Incorrect password for offline login.",
            )

        # Offline login succeeded
        current_user = CurrentUser(
            id=cached.user_id,
            email=cached.email,
            full_name=cached.full_name,
            role=cached.role,
        )
        self._session.set_current_user(current_user)
        self._reset_rate_limit()

        self._logger.info(
            "Offline login: %s from encrypted cache.",
            email,
            extra={
                "event": "OFFLINE_LOGIN",
                "email": email,
                "user_id": cached.user_id,
            },
        )

        return AuthResult(
            success=True,
            user_id=cached.user_id,
            email=cached.email,
            full_name=cached.full_name,
            role=cached.role,
            is_offline_login=True,
        )

    def _classify_login_error(self, exc: Exception) -> AuthResult:
        """Map a Supabase or network exception to a structured
        ``AuthResult`` with a human-readable error message.

        Parameters
        ----------
        exc:
            The exception raised during the login attempt.

        Returns
        -------
        AuthResult
        """
        # Network errors (ConnectionError covers socket-level OSError subclasses)
        if isinstance(exc, (ConnectionError, TimeoutError)):
            self._logger.warning(
                "Network error during login: %s", exc,
                extra={"event": "LOGIN_NETWORK_ERROR"},
            )
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.NETWORK_ERROR,
                error_message=(
                    "Cannot reach the server. Check your internet connection."
                ),
            )

        # Supabase auth errors — inspect the string representation
        error_str = str(exc).lower()

        for code_key, (error_code, human_message) in SUPABASE_ERROR_MAP.items():
            if code_key in error_str:
                self._logger.warning(
                    "Auth error (%s): %s", code_key, exc,
                    extra={"event": "LOGIN_FAILED", "error_code": code_key},
                )

                if error_code != AuthErrorCode.USER_BANNED:
                    self._record_failed_attempt()

                return AuthResult(
                    success=False,
                    error_code=error_code,
                    error_message=human_message,
                )

        # Unknown error
        self._record_failed_attempt()
        self._logger.warning(
            "Unknown login error: %s", exc,
            extra={"event": "LOGIN_FAILED", "error_code": "unknown"},
        )
        return AuthResult(
            success=False,
            error_code=AuthErrorCode.UNKNOWN_ERROR,
            error_message=f"Login failed: {exc}",
        )

    # ==================================================================
    # Registration
    # ==================================================================

    def register(
        self,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
    ) -> AuthResult:
        """Register a new user via Supabase ``sign_up()``.

        Validates all fields client-side before calling the API.
        Passes ``full_name`` and default role ``SALES`` as
        ``user_metadata`` so the Supabase ``handle_new_user`` trigger
        populates the ``profiles`` table automatically.

        Parameters
        ----------
        first_name:
            User's first name.
        last_name:
            User's last name.
        email:
            User's email address.
        password:
            User's chosen password.

        Returns
        -------
        AuthResult
        """
        # --- Client-side validation ---
        name_check = self.validate_name(first_name, "First name")
        if not name_check.is_valid:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.VALIDATION_ERROR,
                error_message=name_check.error_message,
            )

        name_check = self.validate_name(last_name, "Last name")
        if not name_check.is_valid:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.VALIDATION_ERROR,
                error_message=name_check.error_message,
            )

        email_check = self.validate_email(email)
        if not email_check.is_valid:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.VALIDATION_ERROR,
                error_message=email_check.error_message,
            )

        pw_check = self.validate_password(password)
        if not pw_check.is_valid:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.VALIDATION_ERROR,
                error_message=pw_check.error_message,
            )

        email = self.normalize_email(email)
        full_name = f"{first_name.strip()} {last_name.strip()}"

        # --- Supabase sign_up ---
        try:
            self._db.supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                        "role": "SALES",
                    },
                },
            })

            self._logger.info(
                "User registered: %s (%s).",
                full_name,
                email,
                extra={
                    "event": "REGISTER",
                    "email": email,
                    "full_name": full_name,
                },
            )

            return AuthResult(
                success=True,
                email=email,
                full_name=full_name,
            )

        except RuntimeError:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.NETWORK_ERROR,
                error_message=(
                    "Cannot reach the server. "
                    "An internet connection is required to create an account."
                ),
            )

        except Exception as exc:
            return self._classify_registration_error(exc)

    def _classify_registration_error(self, exc: Exception) -> AuthResult:
        """Map a registration exception to a structured ``AuthResult``."""
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.NETWORK_ERROR,
                error_message=(
                    "Cannot reach the server. Check your internet connection."
                ),
            )

        error_str = str(exc).lower()

        for code_key, (error_code, human_message) in SUPABASE_ERROR_MAP.items():
            if code_key in error_str:
                self._logger.warning(
                    "Registration error (%s): %s", code_key, exc,
                    extra={"event": "REGISTER_FAILED", "error_code": code_key},
                )
                return AuthResult(
                    success=False,
                    error_code=error_code,
                    error_message=human_message,
                )

        self._logger.warning(
            "Unknown registration error: %s", exc,
            extra={"event": "REGISTER_FAILED", "error_code": "unknown"},
        )
        return AuthResult(
            success=False,
            error_code=AuthErrorCode.UNKNOWN_ERROR,
            error_message=f"Registration failed: {exc}",
        )

    # ==================================================================
    # Logout
    # ==================================================================

    def logout(self) -> None:
        """Server-side sign-out, clear local state, and log audit event.

        Calls ``supabase.auth.sign_out()`` to revoke the server
        session, then clears the in-memory session and encrypted
        offline cache.  Wraps the server call in ``try/except`` so
        that offline logout still works.
        """
        user_email = "unknown"
        user_id = "unknown"
        if self._session.is_authenticated:
            user = self._session.get_current_user()
            user_email = user.email
            user_id = user.id

        # Server-side revocation
        try:
            self._db.supabase.auth.sign_out()
        except RuntimeError:
            self._logger.debug(
                "Offline — skipping server-side sign_out for %s.", user_email,
            )
        except Exception as exc:
            self._logger.warning(
                "Server-side sign_out failed for %s: %s", user_email, exc,
            )

        # Local cleanup
        self._session.clear()
        self._session_cache.clear_session()

        self._logger.info(
            "User logged out: %s",
            user_email,
            extra={
                "event": "LOGOUT",
                "email": user_email,
                "user_id": user_id,
            },
        )

    # ==================================================================
    # Token refresh
    # ==================================================================

    def refresh_session_token(self) -> AuthResult:
        """Attempt to refresh the access token.

        Distinguishes auth errors (expired/revoked refresh token →
        ``SESSION_EXPIRED``) from transient network errors (silently
        skip, retry next cycle).

        Returns
        -------
        AuthResult
            ``success=True`` when no action was needed or the refresh
            succeeded.  ``success=False`` with
            ``error_code=SESSION_EXPIRED`` when the refresh token is
            permanently invalid.
        """
        if not self._session.is_authenticated:
            return AuthResult(success=True)

        if not self._session.is_token_expired:
            return AuthResult(success=True)

        if not self._db.is_online:
            return AuthResult(success=True)

        refresh_token: Optional[str] = self._session.refresh_token
        if not refresh_token:
            return AuthResult(success=True)

        try:
            response = self._db.supabase.auth.refresh_session(refresh_token)
            new_session = response.session
            if new_session is not None:
                self._session.set_tokens(
                    access_token=new_session.access_token,
                    refresh_token=new_session.refresh_token,
                    expires_at=new_session.expires_at,
                )
                self._logger.info("Session token refreshed.")
            return AuthResult(success=True)

        except (ConnectionError, TimeoutError):
            # Transient network error — retry on next cycle
            self._logger.debug("Network error during token refresh; will retry.")
            return AuthResult(success=True)

        except Exception as exc:
            # Auth error — refresh token is permanently invalid
            self._logger.warning(
                "Token refresh failed (auth error): %s. Forcing logout.", exc,
                extra={"event": "SESSION_EXPIRED"},
            )
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.SESSION_EXPIRED,
                error_message="Your session has expired. Please sign in again.",
            )

    # ==================================================================
    # Password reset
    # ==================================================================

    def request_password_reset(self, email: str) -> AuthResult:
        """Send a password-reset email via Supabase.

        Uses an anti-enumeration response: always shows the same
        success message regardless of whether the email is registered.

        Parameters
        ----------
        email:
            The email address to send the reset link to.

        Returns
        -------
        AuthResult
        """
        email_check = self.validate_email(email)
        if not email_check.is_valid:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.VALIDATION_ERROR,
                error_message=email_check.error_message,
            )

        email = self.normalize_email(email)

        try:
            self._db.supabase.auth.reset_password_for_email(email)
            self._logger.info(
                "Password reset requested for %s.", email,
                extra={"event": "PASSWORD_RESET_REQUESTED", "email": email},
            )
        except RuntimeError:
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.NETWORK_ERROR,
                error_message=(
                    "Cannot reach the server. Check your internet connection."
                ),
            )
        except Exception as exc:
            if isinstance(exc, (ConnectionError, TimeoutError)):
                return AuthResult(
                    success=False,
                    error_code=AuthErrorCode.NETWORK_ERROR,
                    error_message=(
                        "Cannot reach the server. "
                        "Check your internet connection."
                    ),
                )
            self._logger.warning(
                "Password reset error for %s: %s", email, exc,
            )

        # Anti-enumeration: always show generic success
        return AuthResult(
            success=True,
            error_message=(
                "If this email is registered, you will receive "
                "a password reset link."
            ),
        )

