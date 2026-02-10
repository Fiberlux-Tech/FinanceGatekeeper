"""
Encrypted Session Cache Service.

Encrypts and stores Supabase refresh tokens along with user profile data
locally in the SQLite ``encrypted_sessions`` table, enabling offline
authentication without requiring a round-trip to Supabase.

Security model
--------------
- The encryption key is derived at runtime from machine-specific
  characteristics (hostname + OS username) via PBKDF2 with a static salt.
  The key is **never** persisted to disk.
- Payloads are encrypted with AES-256-GCM, providing both confidentiality
  and integrity (authenticated encryption).
- Cached sessions expire after a configurable number of days (default 7).
- Explicit logout deletes the cached row entirely.

Storage layout (single-row table, ``id = 1``)::

    encrypted_sessions
    ├── id               INTEGER PRIMARY KEY  (always 1)
    ├── encrypted_payload BLOB
    ├── nonce            BLOB
    └── tag              BLOB
"""

from __future__ import annotations

import getpass
import json
import socket
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2

from app.database import DatabaseManager
from app.logger import StructuredLogger


# ---------------------------------------------------------------------------
# Pydantic model for the decrypted session payload
# ---------------------------------------------------------------------------

class CachedSession(BaseModel):
    """Represents a decrypted offline session payload.

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
    """

    user_id: str
    email: str
    full_name: str
    role: Literal["SALES", "FINANCE", "ADMIN"]
    refresh_token: str
    cached_at: str  # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SessionCacheService:
    """Manages encrypted offline session persistence.

    After a successful online login, the refresh token and user profile
    are encrypted with AES-256-GCM and stored in the local SQLite
    database.  On subsequent offline boots, the cached session is
    decrypted and validated (expiry check) to allow entry into
    offline mode without re-authenticating with Supabase.

    Security model:
    - Encryption key is derived from machine identity (hostname + OS user)
      via PBKDF2 with a static salt.  The key is never stored.
    - Explicit logout deletes the cached session.
    - Sessions expire after ``max_age_days`` (default 7).

    Parameters
    ----------
    db:
        An initialised ``DatabaseManager`` providing access to the local
        SQLite database.
    logger:
        A ``StructuredLogger`` instance for structured JSON log output.
    max_age_days:
        Maximum number of days a cached session remains valid.  After this
        period the session is considered expired and ``load_cached_session``
        returns ``None``.
    """

    # Static salt used for PBKDF2 key derivation.  Changing this value
    # invalidates all previously cached sessions.
    _PBKDF2_SALT: bytes = b"FinanceGatekeeper_v1_session_salt"
    _PBKDF2_ITERATIONS: int = 100_000
    _KEY_LENGTH: int = 32  # 256 bits

    def __init__(
        self,
        db: DatabaseManager,
        logger: StructuredLogger,
        max_age_days: int = 7,
    ) -> None:
        self._db: DatabaseManager = db
        self._logger: StructuredLogger = logger
        self._max_age_days: int = max_age_days

        # Ensure the encrypted_sessions table exists on construction.
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cache_session(
        self,
        user_id: str,
        email: str,
        full_name: str,
        role: str,
        refresh_token: str,
    ) -> None:
        """Encrypt and persist a session payload for offline use.

        Builds a JSON payload from the supplied user profile fields,
        encrypts it using AES-256-GCM, and upserts the result into the
        ``encrypted_sessions`` table (``id = 1``).

        Parameters
        ----------
        user_id:
            The Supabase UUID of the authenticated user.
        email:
            The user's email address.
        full_name:
            The user's full name for display.
        role:
            The application role string (e.g. ``"ADMIN"``).
        refresh_token:
            The Supabase refresh token.
        """
        payload: dict[str, str] = {
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
            "role": role,
            "refresh_token": refresh_token,
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        plaintext: bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            key: bytes = self._derive_key()
            cipher: AES.GcmMode = AES.new(key, AES.MODE_GCM)  # type: ignore[attr-defined]
            ciphertext: bytes
            tag: bytes
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
            nonce: bytes = cipher.nonce
        except Exception as exc:
            self._logger.error(
                "Failed to encrypt session payload: %s", exc,
            )
            return

        try:
            self._db.sqlite.execute(
                """
                INSERT INTO encrypted_sessions (id, encrypted_payload, nonce, tag)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    encrypted_payload = excluded.encrypted_payload,
                    nonce             = excluded.nonce,
                    tag               = excluded.tag
                """,
                (ciphertext, nonce, tag),
            )
            self._db.sqlite.commit()
            self._logger.info(
                "Session cached for user %s (%s).", full_name, email,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to write encrypted session to database: %s", exc,
            )

    def load_cached_session(self) -> Optional[CachedSession]:
        """Load and decrypt the cached offline session.

        Reads the single cached row from ``encrypted_sessions``, decrypts
        the payload, validates its expiry, and returns a ``CachedSession``
        model.

        Returns
        -------
        CachedSession or None
            The decrypted session if it exists, is valid, and has not
            expired.  ``None`` is returned when:

            - No cached session row exists.
            - Decryption fails (corrupted data or machine identity changed).
            - The session has exceeded ``max_age_days``.
        """
        try:
            row = self._db.sqlite.execute(
                "SELECT encrypted_payload, nonce, tag FROM encrypted_sessions WHERE id = 1",
            ).fetchone()
        except Exception as exc:
            self._logger.warning(
                "Failed to read cached session from database: %s", exc,
            )
            return None

        if row is None:
            self._logger.debug("No cached session found.")
            return None

        encrypted_payload: bytes = row["encrypted_payload"]
        nonce: bytes = row["nonce"]
        tag: bytes = row["tag"]

        # --- Decrypt ---
        try:
            key: bytes = self._derive_key()
            cipher: AES.GcmMode = AES.new(key, AES.MODE_GCM, nonce=nonce)  # type: ignore[attr-defined]
            plaintext: bytes = cipher.decrypt_and_verify(encrypted_payload, tag)
        except (ValueError, KeyError) as exc:
            self._logger.warning(
                "Decryption of cached session failed (corrupted data or "
                "machine identity changed): %s",
                exc,
            )
            return None
        except Exception as exc:
            self._logger.warning(
                "Unexpected error during session decryption: %s", exc,
            )
            return None

        # --- Deserialize ---
        try:
            data: dict[str, str] = json.loads(plaintext.decode("utf-8"))
            session = CachedSession(**data)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            self._logger.warning(
                "Cached session payload is malformed: %s", exc,
            )
            return None

        # --- Expiry check ---
        try:
            cached_at: datetime = datetime.fromisoformat(session.cached_at)
            expiry: datetime = cached_at + timedelta(days=self._max_age_days)
            if datetime.now(tz=timezone.utc) > expiry:
                self._logger.info(
                    "Cached session for user %s has expired (cached at %s, "
                    "max age %d days).",
                    session.full_name,
                    session.cached_at,
                    self._max_age_days,
                )
                return None
        except (ValueError, TypeError) as exc:
            self._logger.warning(
                "Could not parse cached_at timestamp '%s': %s",
                session.cached_at,
                exc,
            )
            return None

        self._logger.info(
            "Loaded cached session for user %s (%s).",
            session.full_name,
            session.email,
        )
        return session

    def clear_session(self) -> None:
        """Delete the cached session from the local database.

        This is called during explicit logout to ensure that the
        encrypted refresh token is removed from the machine.  Safe to
        call even if no cached session exists.
        """
        try:
            self._db.sqlite.execute(
                "DELETE FROM encrypted_sessions WHERE id = 1",
            )
            self._db.sqlite.commit()
            self._logger.info("Cached session cleared.")
        except Exception as exc:
            self._logger.error(
                "Failed to clear cached session: %s", exc,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _derive_key(self) -> bytes:
        """Derive a 256-bit AES key from machine identity via PBKDF2.

        The key is deterministic for a given (hostname, OS username) pair
        and is **never** stored on disk.  If the machine identity changes
        (e.g. the user logs in under a different OS account), previously
        cached sessions become undecryptable and are treated as corrupted.

        Returns
        -------
        bytes
            A 32-byte (256-bit) key suitable for AES-256-GCM.
        """
        password: str = f"{socket.gethostname()}:{getpass.getuser()}"
        key: bytes = PBKDF2(
            password=password,
            salt=self._PBKDF2_SALT,
            dkLen=self._KEY_LENGTH,
            count=self._PBKDF2_ITERATIONS,
        )
        return key

    def _ensure_table(self) -> None:
        """Create the ``encrypted_sessions`` table if it does not exist.

        This is idempotent and safe to call on every service construction.
        The table uses a single-row design (``id = 1``) to store exactly
        one active session at a time.
        """
        try:
            self._db.sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS encrypted_sessions (
                    id                INTEGER PRIMARY KEY,
                    encrypted_payload BLOB NOT NULL,
                    nonce             BLOB NOT NULL,
                    tag               BLOB NOT NULL
                )
                """
            )
            self._db.sqlite.commit()
            self._logger.debug("Ensured encrypted_sessions table exists.")
        except Exception as exc:
            self._logger.error(
                "Failed to create encrypted_sessions table: %s", exc,
            )
