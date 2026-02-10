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

from supabase import create_client, Client as SupabaseClient

from app.logger import StructuredLogger


class DatabaseManager:
    """Manages connections to the local SQLite database and cloud Supabase instance.

    Fully configured at construction time via dependency injection.  No
    separate ``init_*`` methods are required.

    Parameters
    ----------
    supabase_url:
        The Supabase project URL (e.g. ``https://xyz.supabase.co``).
    supabase_key:
        The Supabase anonymous / service-role key.
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
        self._supabase: SupabaseClient = create_client(supabase_url, supabase_key)
        self._sqlite_conn: sqlite3.Connection = self._connect_sqlite(sqlite_path)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def supabase(self) -> SupabaseClient:
        """Return the initialised Supabase client."""
        return self._supabase

    @property
    def sqlite(self) -> sqlite3.Connection:
        """Return the initialised SQLite connection."""
        return self._sqlite_conn

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

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
