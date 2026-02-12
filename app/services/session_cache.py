"""
Encrypted Session Cache Service.

Encrypts and stores Supabase refresh tokens along with user profile data
locally in the SQLite ``encrypted_sessions`` table, enabling offline
authentication without requiring a round-trip to Supabase.

Security model
--------------
- The encryption key is derived at runtime from machine-specific
  characteristics (hostname + OS username) via PBKDF2-HMAC-SHA256 with
  a per-machine random salt.  The key is **never** persisted to disk.
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
import hashlib
import hmac
import json
import os
import platform
import stat
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.models.auth_models import CachedSession

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2

from app.database import DatabaseManager
from app.logger import StructuredLogger


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
      via PBKDF2-HMAC-SHA256 with a per-machine random salt stored in
      ``~/.fingate_session_salt``.  The key is never stored.  If the salt
      file cannot be created, session caching is refused entirely.
    - Explicit logout deletes the cached session.
    - Sessions expire after ``max_age_days`` (default 7).

    Architecture Note
    -----------------
    This service intentionally accesses SQLite directly rather than
    through a Repository, because the encrypted session is
    infrastructure state (auth tokens), not domain data (transactions,
    users).  This is a documented exception to the Repository pattern.

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

    _PBKDF2_ITERATIONS: int = 600_000
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

        # The encrypted_sessions table is created by schema.py during
        # initialize_schema() — no duplicate DDL here.

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
        password_hash: Optional[str] = None,
        password_salt: Optional[str] = None,
    ) -> bool:
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
        password_hash:
            Hex-encoded PBKDF2-HMAC-SHA256 hash of the user's password
            for offline login verification.  ``None`` disables offline
            password verification (forces online re-auth).
        password_salt:
            Hex-encoded random 32-byte salt used to derive the password
            hash.  Must be supplied together with *password_hash*.

        Returns
        -------
        bool
            ``True`` if the session was successfully encrypted and
            persisted.  ``False`` if encryption or database write
            failed (the error is logged but not raised, since session
            caching is non-critical to the login flow).
        """
        payload: dict[str, Optional[str]] = {
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
            "role": role,
            "refresh_token": refresh_token,
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
            "password_hash": password_hash,
            "password_salt": password_salt,
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
            self._logger.warning(
                "Failed to encrypt session payload: %s", exc,
            )
            return False

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
            return True
        except Exception as exc:
            self._logger.warning(
                "Failed to write encrypted session to database: %s", exc,
            )
            return False

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

    def verify_offline_password(
        self,
        email: str,
        password: str,
    ) -> Optional[CachedSession]:
        """Load the cached session and verify the entered password.

        Combines ``load_cached_session()`` with PBKDF2-HMAC-SHA256
        password verification so that offline login requires *both* a
        matching email and the correct password.

        Parameters
        ----------
        email:
            The email address entered by the user.
        password:
            The plaintext password entered by the user.

        Returns
        -------
        CachedSession or None
            The decrypted session if the email matches and the password
            hash verifies.  ``None`` is returned when:

            - No cached session exists or it has expired.
            - The cached email does not match the entered email.
            - The cached session has no stored password hash (pre-Phase
              1.5 cache — forces online re-authentication).
            - The entered password does not match the stored hash.
        """
        cached = self.load_cached_session()
        if cached is None or cached.email != email:
            return None

        if cached.password_hash is None or cached.password_salt is None:
            self._logger.warning(
                "Cached session for %s has no password hash. "
                "Online login required.",
                email,
            )
            return None

        salt_bytes: bytes = bytes.fromhex(cached.password_salt)
        computed_hash: str = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_bytes,
            iterations=self._PBKDF2_ITERATIONS,
        ).hex()

        if not hmac.compare_digest(computed_hash, cached.password_hash):
            self._logger.warning(
                "Offline password verification failed for %s.", email,
            )
            return None

        self._logger.info("Offline password verified for %s.", email)
        return cached

    @staticmethod
    def hash_password(password: str) -> tuple[str, str]:
        """Derive a PBKDF2-HMAC-SHA256 hash for offline password storage.

        Parameters
        ----------
        password:
            The plaintext password to hash.

        Returns
        -------
        tuple[str, str]
            A ``(hex_hash, hex_salt)`` pair.  The salt is 32 random
            bytes; the hash is derived with 600 000 PBKDF2 iterations
            (OWASP 2023 recommendation).
        """
        salt: bytes = os.urandom(32)
        pw_hash: str = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations=SessionCacheService._PBKDF2_ITERATIONS,
        ).hex()
        return pw_hash, salt.hex()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _derive_key(self) -> bytes:
        """Derive a 256-bit AES key from machine identity via PBKDF2-HMAC-SHA256.

        Uses a per-machine random salt stored in the user's home
        directory.  If the salt file cannot be created or read, an
        ``OSError`` propagates to the caller — session caching is
        refused rather than degrading to a weak static salt.

        The key is deterministic for a given (hostname, OS username,
        salt) triple and is **never** stored on disk.  If the machine
        identity changes (e.g. the user logs in under a different OS
        account), previously cached sessions become undecryptable and
        are treated as corrupted.

        Threat Model
        ------------
        This is a **local desktop application**.  The encryption key
        protects cached session tokens against casual disk access — for
        example, a colleague with brief physical access or an IT
        administrator browsing user directories.  It is NOT designed to
        resist:

        - A sophisticated attacker with persistent local access (they
          can attach a debugger, read process memory, or install a
          keylogger).
        - An attacker who has compromised the OS user account (they can
          derive the same key).

        Key material: ``hostname:username`` provides machine-binding so
        that a copied database file is useless on a different machine.
        The real entropy comes from the **per-machine random 32-byte
        salt** stored in ``~/.fingate_session_salt``, which is unique
        per installation.

        Future hardening (v2+): Windows DPAPI (``CryptProtectData``)
        would bind the key to the Windows user's login credentials,
        providing protection even against file-level access by other OS
        users.  Deferred because it requires platform-specific ctypes
        code and the current threat model is appropriate for a v1
        corporate desktop deployment.

        Returns
        -------
        bytes
            A 32-byte (256-bit) key suitable for AES-256-GCM.

        Raises
        ------
        OSError
            If the per-machine salt file cannot be created or read.
        """
        password: str = f"{socket.gethostname()}:{getpass.getuser()}"
        salt: bytes = self._get_or_create_salt()
        key: bytes = PBKDF2(
            password=password,
            salt=salt,
            dkLen=self._KEY_LENGTH,
            count=self._PBKDF2_ITERATIONS,
            hmac_hash_module=SHA256,
        )
        return key

    def _restrict_windows_acl(self, file_path: Path) -> None:
        """Set NTFS ACLs on *file_path* to restrict access to the current user.

        Uses the ``icacls`` utility to:

        1. Remove all inherited permissions (``/inheritance:r``).
        2. Grant full control only to the current OS user
           (``/grant:r <username>:F``).

        This is the Windows equivalent of ``chmod 0o600``.  If the
        command fails for any reason (icacls not found, permission
        denied, timeout), a warning is logged but execution continues
        because the ACL restriction is defense-in-depth — the salt
        file remains usable without it.
        """
        try:
            username: str = getpass.getuser()
            result: subprocess.CompletedProcess[bytes] = subprocess.run(
                [
                    "icacls",
                    str(file_path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{username}:F",
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                stderr_text: str = result.stderr.decode("utf-8", errors="replace").strip()
                self._logger.warning(
                    "icacls returned non-zero exit code %d for '%s': %s",
                    result.returncode,
                    file_path,
                    stderr_text,
                )
            else:
                self._logger.info(
                    "Windows ACLs restricted to current user on '%s'.",
                    file_path,
                )
        except Exception as exc:
            self._logger.warning(
                "Failed to set Windows ACLs on '%s': %s",
                file_path,
                exc,
            )

    def _get_or_create_salt(self) -> bytes:
        """Return a per-machine random salt, creating it on first run.

        The salt is stored at ``~/.fingate_session_salt``.  If the file
        cannot be read or created (e.g. permissions), an ``OSError`` is
        raised — callers must handle this to refuse session caching
        rather than falling back to a weak static salt.

        Raises
        ------
        OSError
            If the salt file cannot be read or written.
        """
        salt_path: Path = Path.home() / ".fingate_session_salt"
        if salt_path.exists():
            data: bytes = salt_path.read_bytes()
            if len(data) == 32:
                return data
            # Corrupt or wrong-length — regenerate
            self._logger.warning(
                "Salt file has unexpected length (%d); regenerating.",
                len(data),
            )
        salt: bytes = os.urandom(32)
        salt_path.write_bytes(salt)

        # Restrict file permissions to owner-only.
        if platform.system() == "Windows":
            self._restrict_windows_acl(salt_path)
        else:
            salt_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600

        self._logger.info("Per-machine session salt created at %s.", salt_path)
        return salt

