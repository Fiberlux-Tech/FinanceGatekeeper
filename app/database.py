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
from pathlib import Path
from typing import Optional

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

        # --- Supabase (optional — offline-first) ---
        self._supabase: Optional[SupabaseClient] = None
        if supabase_url and supabase_key:
            try:
                self._supabase = create_client(supabase_url, supabase_key)
                self._logger.info("Supabase client initialized.")
            except Exception as exc:
                self._logger.warning(
                    "Supabase initialization failed: %s. Running in offline mode.",
                    exc,
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

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def get_pending_sync_count(self) -> int:
        """Return the number of pending items in the sync queue.

        Returns ``0`` when the table does not exist yet or the query
        fails for any reason, making it safe to call at any point
        during the application lifecycle.
        """
        try:
            row = self._sqlite_conn.execute(
                "SELECT COUNT(*) AS cnt FROM sync_queue WHERE status = 'pending'",
            ).fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            return 0

    def close(self) -> None:
        """Close the local SQLite connection.

        Safe to call multiple times; subsequent calls are no-ops.
        """
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
