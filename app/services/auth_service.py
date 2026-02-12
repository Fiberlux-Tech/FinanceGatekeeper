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

import hashlib
import hmac
import platform
import re
import socket
import getpass
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.auth import SessionManager
from app.models.user import User
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.auth_models import (
    AuthErrorCode,
    AuthResult,
    RateLimitState,
    RateLimitStore,
    SUPABASE_ERROR_MAP,
    ValidationResult,
)
from app.models.enums import UserRole
from app.repositories.user_repository import UserRepository
from app.services.jit_provisioning import JITProvisioningService
from app.utils.general import secure_clear_string
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

# Matches C0 controls (U+0000–U+001F), DEL (U+007F), and C1 controls (U+0080–U+009F).
_CONTROL_CHAR_RE: re.Pattern[str] = re.compile(r"[\x00-\x1f\x7f-\x9f]")


# ---------------------------------------------------------------------------
# DPAPI HMAC-key helper (Windows only)
# ---------------------------------------------------------------------------

_DPAPI_KEY_PATH: Path = Path.home() / ".fingate_hmac_key"


def _get_dpapi_hmac_key() -> Optional[bytes]:
    """Return a 32-byte HMAC secret protected by Windows DPAPI.

    On the first call the function generates 32 cryptographically random
    bytes, encrypts them with ``CryptProtectData`` (bound to the current
    Windows user's login credentials), and persists the encrypted blob to
    ``~/.fingate_hmac_key``.

    On subsequent calls it reads the blob, decrypts it with
    ``CryptUnprotectData``, and returns the original 32-byte secret.

    Returns ``None`` on non-Windows platforms or if any DPAPI operation
    fails (the caller should fall back to the legacy key derivation).
    """
    if platform.system() != "Windows":
        return None

    # Lazy import — ctypes.windll is only available on Windows.
    import ctypes
    import ctypes.wintypes

    class _DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _encrypt(plain: bytes) -> bytes:
        """Encrypt *plain* with DPAPI and return the cipher blob."""
        blob_in = _DATA_BLOB(len(plain), ctypes.create_string_buffer(plain, len(plain)))
        blob_out = _DATA_BLOB()
        if not _crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,   # description (unused)
            None,   # optional entropy
            None,   # reserved
            None,   # prompt struct
            0,      # flags
            ctypes.byref(blob_out),
        ):
            raise OSError("CryptProtectData failed")
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return encrypted

    def _decrypt(cipher: bytes) -> bytes:
        """Decrypt a DPAPI cipher blob and return the plaintext."""
        blob_in = _DATA_BLOB(len(cipher), ctypes.create_string_buffer(cipher, len(cipher)))
        blob_out = _DATA_BLOB()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,   # description out
            None,   # optional entropy
            None,   # reserved
            None,   # prompt struct
            0,      # flags
            ctypes.byref(blob_out),
        ):
            raise OSError("CryptUnprotectData failed")
        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return decrypted

    # --- Try to read an existing DPAPI-protected key file ----------------
    try:
        if _DPAPI_KEY_PATH.is_file():
            cipher_blob = _DPAPI_KEY_PATH.read_bytes()
            return _decrypt(cipher_blob)
    except OSError:
        # Decryption failed (different user, corrupted file, etc.)
        # Fall through to regeneration below.
        pass

    # --- Generate, protect, and persist a fresh 32-byte secret -----------
    try:
        import secrets
        raw_secret: bytes = secrets.token_bytes(32)
        cipher_blob = _encrypt(raw_secret)
        _DPAPI_KEY_PATH.write_bytes(cipher_blob)
        return raw_secret
    except OSError:
        return None


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
        user_repo: Optional[UserRepository] = None,
    ) -> None:
        self._db: DatabaseManager = db
        self._session: SessionManager = session
        self._jit_service: JITProvisioningService = jit_service
        self._session_cache: SessionCacheService = session_cache
        self._logger: StructuredLogger = logger
        self._user_repo: Optional[UserRepository] = user_repo

        # Per-user rate-limit state — persisted to app_settings so
        # lockouts survive application restarts.
        self._rate_limit_store: RateLimitStore = RateLimitStore()
        self._rate_lock: threading.Lock = threading.Lock()
        self._load_rate_limit_state()

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

        Rejects control characters (U+0000–U+001F, U+007F–U+009F)
        including newlines and tabs to prevent log injection and
        display corruption.

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
        if _CONTROL_CHAR_RE.search(stripped):
            return ValidationResult(
                is_valid=False,
                error_message=(
                    f"{field_label} contains invalid characters. "
                    "Only printable characters are allowed."
                ),
            )
        return ValidationResult(is_valid=True)

    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalise an email address: strip whitespace and lowercase."""
        return email.strip().lower()

    # ==================================================================
    # Rate limiting
    # ==================================================================

    def check_rate_limit(self, email: str) -> tuple[bool, int]:
        """Check whether *email* is currently rate-limited.

        Parameters
        ----------
        email:
            Normalised email address of the user being checked.

        Returns
        -------
        tuple[bool, int]
            ``(is_locked, remaining_seconds)``.  When ``is_locked`` is
            ``False``, ``remaining_seconds`` is ``0``.
        """
        with self._rate_lock:
            state: RateLimitState = self._rate_limit_store.entries.get(
                email, RateLimitState(),
            )

            if state.lockout_until is None:
                return False, 0

            now = datetime.now(tz=timezone.utc)
            if now >= state.lockout_until:
                # Inline reset to avoid re-entrant lock acquisition.
                self._rate_limit_store.entries.pop(email, None)
                self._persist_rate_limit_state()
                return False, 0

            remaining = int((state.lockout_until - now).total_seconds()) + 1
            return True, remaining

    def _record_failed_attempt(self, email: str) -> None:
        """Record a failed login attempt for *email* and engage the
        lockout if the threshold is reached.

        Parameters
        ----------
        email:
            Normalised email address of the user.
        """
        with self._rate_lock:
            state: RateLimitState = self._rate_limit_store.entries.get(
                email, RateLimitState(),
            )
            state.failed_attempts += 1
            if state.failed_attempts >= _MAX_FAILED_ATTEMPTS:
                state.lockout_until = (
                    datetime.now(tz=timezone.utc) + timedelta(seconds=_LOCKOUT_SECONDS)
                )
                self._logger.warning(
                    "Rate limit engaged for %s: %d failed attempts. Locked for %ds.",
                    email,
                    state.failed_attempts,
                    _LOCKOUT_SECONDS,
                )
            self._rate_limit_store.entries[email] = state
            self._persist_rate_limit_state()

    def _reset_rate_limit(self, email: str) -> None:
        """Clear the rate-limit counters for *email* after a successful
        login or after the lockout period expires.

        Parameters
        ----------
        email:
            Normalised email address of the user.
        """
        with self._rate_lock:
            self._rate_limit_store.entries.pop(email, None)
            self._persist_rate_limit_state()

    def _load_rate_limit_state(self) -> None:
        """Restore per-user rate-limit state from ``app_settings`` on
        startup, deserializing via ``RateLimitStore``.

        Verifies the HMAC signature appended by ``_persist_rate_limit_state``
        to detect file-level tampering (L-48).  If the signature is missing
        or invalid, the state is reset to empty — this is the conservative
        choice because it only means lockouts are cleared, never that
        lockouts are bypassed on the *current* session.
        """
        try:
            row = self._db.sqlite.execute(
                "SELECT value FROM app_settings WHERE key = 'rate_limit_state'",
            ).fetchone()
            if row is None:
                return

            raw_value: str = row["value"]
            separator = "|hmac:"
            if separator not in raw_value:
                # Legacy format (pre-HMAC) — discard untrusted state.
                self._logger.warning(
                    "Rate-limit state has no HMAC signature; resetting.",
                )
                return

            json_part, hmac_hex = raw_value.rsplit(separator, maxsplit=1)

            expected_hmac = self._compute_rate_limit_hmac(json_part)
            if not hmac.compare_digest(expected_hmac, hmac_hex):
                self._logger.warning(
                    "Rate-limit state HMAC verification failed; "
                    "possible tampering. Resetting to empty.",
                )
                return

            with self._rate_lock:
                self._rate_limit_store = RateLimitStore.model_validate_json(
                    json_part,
                )
        except Exception as exc:
            self._logger.warning("Could not load rate-limit state: %s", exc)

    def _persist_rate_limit_state(self) -> None:
        """Write current per-user rate-limit state to ``app_settings``.

        Appends an HMAC-SHA256 signature derived from machine identity
        so that an attacker with file access cannot silently reset
        lockout counters (L-48).

        Caller MUST already hold ``self._rate_lock``.
        """
        try:
            json_payload: str = self._rate_limit_store.model_dump_json()
            hmac_hex: str = self._compute_rate_limit_hmac(json_payload)
            signed_value: str = f"{json_payload}|hmac:{hmac_hex}"

            self._db.sqlite.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ('rate_limit_state', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (signed_value,),
            )
            self._db.sqlite.commit()
        except Exception as exc:
            self._logger.warning("Could not persist rate-limit state: %s", exc)

    @staticmethod
    def _compute_rate_limit_hmac(payload: str) -> str:
        """Compute an HMAC-SHA256 over *payload* using machine identity.

        On Windows the HMAC key is a 32-byte random secret protected by
        the Windows Data Protection API (DPAPI).  DPAPI binds the secret
        to the current Windows user's login credentials, making it
        unreadable by other OS users even with direct file access.

        On non-Windows platforms (or if the DPAPI operation fails) the
        key falls back to ``hostname:username`` — the same identity
        material used by ``SessionCacheService._derive_key``.

        Parameters
        ----------
        payload:
            The JSON string to sign.

        Returns
        -------
        str
            Hex-encoded HMAC-SHA256 digest.
        """
        dpapi_key: Optional[bytes] = _get_dpapi_hmac_key()
        if dpapi_key is not None:
            return hmac.new(
                dpapi_key,
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

        machine_key: str = f"{socket.gethostname()}:{getpass.getuser()}"
        return hmac.new(
            machine_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

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
        is_locked, remaining = self.check_rate_limit(email)
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

            # Display name from metadata (non-privileged field)
            display_name: str = user_metadata.get(
                "full_name", email.split("@")[0],
            )

            # SECURITY (C-1): Fetch authoritative role from the profiles
            # table, NOT from user_metadata (which is user-controlled at
            # signup).  The DB-level trigger always assigns SALES for new
            # users; only an ADMIN can escalate via service_role key.
            authoritative_role: str = UserRole.SALES
            if self._user_repo is not None:
                db_user = self._user_repo.get_by_id(user_data.id)
                if db_user is not None:
                    authoritative_role = str(db_user.role)
            # If no profile row exists yet (first login), default SALES.
            # JIT provisioning below will create the row with SALES.

            current_user = User(
                id=user_data.id,
                email=user_data.email or email,
                full_name=display_name,
                role=authoritative_role,
            )

            # Session + tokens
            self._session.set_current_user(current_user)
            self._session.set_tokens(
                access_token=session_data.access_token,
                refresh_token=session_data.refresh_token,
                expires_at=session_data.expires_at,
            )

            # JIT provisioning — syncs email and full_name.
            # Role is never overwritten (C-1 security decision).
            self._jit_service.ensure_user_synced(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
            )

            # Hash password for offline verification
            pw_hash, pw_salt = SessionCacheService.hash_password(password)

            # Cache session with password hash
            cached_ok: bool = self._session_cache.cache_session(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
                refresh_token=session_data.refresh_token,
                password_hash=pw_hash,
                password_salt=pw_salt,
            )
            if not cached_ok:
                self._logger.warning(
                    "Session caching failed for %s — offline login "
                    "will be unavailable until next successful cache.",
                    current_user.email,
                )

            self._reset_rate_limit(email)

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
            return self._classify_login_error(exc, email)

        finally:
            # L-51: Best-effort clearing of password from memory.
            secure_clear_string(password)

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
                # L-49: Record the failed attempt even for email mismatch
                # to prevent brute-force email enumeration while offline.
                self._record_failed_attempt(email)
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
            self._record_failed_attempt(email)
            return AuthResult(
                success=False,
                error_code=AuthErrorCode.INVALID_CREDENTIALS,
                error_message=(
                    "Offline login failed. "
                    "Please check your credentials or connect to the internet."
                ),
            )

        # Offline login succeeded
        current_user = User(
            id=cached.user_id,
            email=cached.email,
            full_name=cached.full_name,
            role=cached.role,
        )
        self._session.set_current_user(current_user)
        self._reset_rate_limit(email)

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

    def _classify_login_error(self, exc: Exception, email: str) -> AuthResult:
        """Map a Supabase or network exception to a structured
        ``AuthResult`` with a human-readable error message.

        Parameters
        ----------
        exc:
            The exception raised during the login attempt.
        email:
            Normalised email address of the user, used for per-user
            rate-limit tracking.

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
                    self._record_failed_attempt(email)

                return AuthResult(
                    success=False,
                    error_code=error_code,
                    error_message=human_message,
                )

        # Unknown error
        self._record_failed_attempt(email)
        self._logger.warning(
            "Unknown login error: %s", exc,
            extra={"event": "LOGIN_FAILED", "error_code": "unknown"},
        )
        return AuthResult(
            success=False,
            error_code=AuthErrorCode.UNKNOWN_ERROR,
            error_message="An unexpected error occurred. Please try again later.",
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

        finally:
            # L-51: Best-effort clearing of password from memory.
            secure_clear_string(password)

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
            error_message="Registration could not be completed. Please try again later.",
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

