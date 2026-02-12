"""
Database Abstraction Layer.

Implements the dual-database pattern for the FinanceGatekeeper OS:

- **SQLite (local)**: The offline-first primary store. All writes land here
  first, ensuring the application remains fully functional without network
  connectivity.  Also serves as the sync queue for outbound changes.

- **Supabase (cloud PostgreSQL)**: The authoritative remote store. A
  background sync service reconciles local SQLite state with Supabase when
  connectivity is available.

Data access is performed through the Repository pattern.  This module only
manages the raw database *connections*; it contains no query logic.

Security Note — Encryption at Rest (L-50)
------------------------------------------
The local SQLite database is **not** encrypted at rest.  Transaction data,
user profiles, and audit logs stored in ``gatekeeper_local.db`` are readable
by any process or user with file-system access to the database file.

Current mitigations:
- Sensitive auth tokens are AES-256-GCM encrypted in the
  ``encrypted_sessions`` table (see ``SessionCacheService``).
- Rate-limit state is HMAC-signed to detect file-level tampering.
- The application targets corporate Windows desktops with NTFS ACLs
  restricting per-user home directories.

Planned hardening (v2+):
- Migrate to ``sqlcipher`` (SQLCipher) for full database encryption,
  keyed via Windows DPAPI (``CryptProtectData``).  This requires a
  native C extension and installer changes — deferred to avoid
  deployment complexity in v1.

Usage (dependency injection at app startup)::

    from app.database import DatabaseManager
    from app.logger import StructuredLogger

    db = DatabaseManager(
        supabase_url=settings.SUPABASE_URL,
        supabase_key=settings.SUPABASE_KEY,
        sqlite_path=Path("gatekeeper_local.db"),
        logger=StructuredLogger(name="database"),
    )
    # Inject `db` into repositories / services that need it.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from supabase import create_client, Client as SupabaseClient

from app.logger import StructuredLogger


class DatabaseManager:
    """Manages connections to the local SQLite database and cloud Supabase instance.

    Fully configured at construction time via dependency injection.  No
    separate ``init_*`` methods are required.

    When ``supabase_url`` or ``supabase_key`` is empty the Supabase client
    is **not** created and the application runs in offline mode.  All
    repository code already wraps Supabase calls in ``try/except`` with
    SQLite fallback, so the ``RuntimeError`` raised by the ``supabase``
    property is caught naturally by the existing error-handling paths.

    Parameters
    ----------
    supabase_url:
        The Supabase project URL (e.g. ``https://xyz.supabase.co``).
        May be empty to run in offline mode.
    supabase_key:
        The Supabase anonymous / service-role key.
        May be empty to run in offline mode.
    sqlite_path:
        Filesystem path for the local SQLite database file.  Parent
        directories must already exist.
    logger:
        A ``StructuredLogger`` instance for structured JSON log output.
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        sqlite_path: Path,
        logger: StructuredLogger,
    ) -> None:
        self._logger: StructuredLogger = logger
        self._write_lock: threading.RLock = threading.RLock()
        self._in_batch: bool = False

        # --- Supabase (optional — offline-first) ---
        self._supabase: Optional[SupabaseClient] = None
        if supabase_url and supabase_key:
            try:
                self._supabase = create_client(supabase_url, supabase_key)
                self._logger.info("Supabase client initialized.")
            except (ValueError, TypeError) as exc:
                self._logger.warning(
                    "Supabase credential format error: %s. Running in offline mode.",
                    exc,
                )
            except Exception as exc:
                self._logger.error(
                    "Unexpected Supabase initialization failure: %s. "
                    "Running in offline mode.",
                    exc,
                    exc_info=True,
                )
        else:
            self._logger.warning(
                "Supabase credentials not configured — running in offline mode."
            )

        # --- SQLite (always required) ---
        self._sqlite_conn: sqlite3.Connection = self._connect_sqlite(sqlite_path)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def supabase(self) -> SupabaseClient:
        """Return the initialised Supabase client.

        Raises
        ------
        RuntimeError
            If the Supabase client was not initialised (offline mode).
            All repository code wraps Supabase calls in ``try/except``
            so this exception triggers the SQLite fallback path.
        """
        if self._supabase is None:
            raise RuntimeError(
                "Supabase client is not initialised. "
                "The application is running in offline mode."
            )
        return self._supabase

    @property
    def is_online(self) -> bool:
        """``True`` when the Supabase client is available."""
        return self._supabase is not None

    @property
    def sqlite(self) -> sqlite3.Connection:
        """Return the initialised SQLite connection."""
        return self._sqlite_conn

    @property
    def write_lock(self) -> threading.RLock:
        """Return the write lock for thread-safe SQLite operations.

        All code that performs SQLite writes (INSERT, UPDATE, DELETE,
        or any operation followed by ``commit()``) should acquire this
        lock first::

            with db.write_lock:
                db.sqlite.execute("INSERT ...")
                db.sqlite.commit()
        """
        return self._write_lock

    @property
    def in_batch(self) -> bool:
        """``True`` when a :meth:`batch_write` context is active.

        Repository code checks this flag before issuing ``commit()``
        so that bulk operations can defer the commit to a single call
        at the end of the batch.
        """
        return self._in_batch

    @contextmanager
    def batch_write(self) -> Generator[None, None, None]:
        """Context manager that defers SQLite commits for bulk operations.

        While the context is active, :pyattr:`in_batch` is ``True`` and
        repository ``_commit()`` calls become no-ops.  On normal exit
        a single ``commit()`` is issued.  On exception the transaction
        is rolled back and the error re-raised.

        Backwards-compatible: without this context manager, individual
        commits still work exactly as before.

        Example::

            with db_manager.batch_write():
                for item in items:
                    repo.create(item)  # no commit per item
            # single commit happens here
        """
        if self._in_batch:
            # Re-entrant: already in a batch, just yield without
            # double-committing on exit.
            yield
            return

        self._in_batch = True
        try:
            yield
            self._sqlite_conn.commit()
            self._logger.debug("Batch write committed.")
        except Exception:
            self._sqlite_conn.rollback()
            self._logger.error(
                "Batch write rolled back due to exception.", exc_info=True,
            )
            raise
        finally:
            self._in_batch = False

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def get_pending_sync_count(self) -> int:
        """Return the number of pending items in the sync queue.

        Returns ``0`` when the table does not exist yet or the query
        fails for any reason, making it safe to call at any point
        during the application lifecycle.
        """
        with self._write_lock:
            try:
                row = self._sqlite_conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sync_queue WHERE status = 'pending'",
                ).fetchone()
                return int(row["cnt"]) if row else 0
            except Exception:
                self._logger.debug(
                    "get_pending_sync_count query failed; returning 0.",
                    exc_info=True,
                )
                return 0

    def close(self) -> None:
        """Close the local SQLite connection.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        with self._write_lock:
            if self._sqlite_conn is not None:
                try:
                    self._sqlite_conn.close()
                    self._logger.info("SQLite connection closed.")
                except sqlite3.ProgrammingError:
                    # Connection was already closed — nothing to do.
                    pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _connect_sqlite(self, path: Path) -> sqlite3.Connection:
        """Open (or create) a SQLite database with defensive error handling.

        Handles ``PermissionError`` when the file or its parent directory is
        locked or read-only, and re-raises with a user-friendly message so
        that the UI layer can present a helpful warning.

        Parameters
        ----------
        path:
            Filesystem path for the SQLite database file.

        Returns
        -------
        sqlite3.Connection
            A configured connection with ``row_factory`` set to
            ``sqlite3.Row`` for dict-like row access.

        Raises
        ------
        PermissionError
            If the OS denies access to the database file or its directory.
        """
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent read performance.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            self._logger.info("SQLite database opened at %s", path)
            return conn
        except PermissionError as exc:
            msg = (
                f"Cannot open the local database at '{path}'. "
                "The file or its directory may be read-only or locked by "
                "another process.  Please check file permissions and try again."
            )
            self._logger.error(msg)
            raise PermissionError(msg) from exc
