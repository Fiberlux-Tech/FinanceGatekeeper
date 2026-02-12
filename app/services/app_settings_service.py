"""
Application Settings Service.

Read/write access to the ``app_settings`` key-value table in the local
SQLite database.  Provides typed getters for known settings and a
generic get/set for future extensibility.

This is a documented exception to the Repository pattern (like
``SessionCacheService``) because ``app_settings`` stores infrastructure
state, not domain data.

The ``app_settings`` table already exists at schema version 7::

    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

from __future__ import annotations

from typing import Optional

from app.database import DatabaseManager
from app.logger import StructuredLogger

_KEY_SHAREPOINT_ROOT: str = "sharepoint_root_path"


class AppSettingsService:
    """Manages persistent application preferences in local SQLite.

    Parameters
    ----------
    db:
        Initialised ``DatabaseManager`` with active SQLite connection.
    logger:
        Structured logger instance.
    """

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        self._db = db
        self._logger = logger

    # ------------------------------------------------------------------
    # Generic key-value access
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        """Read a setting value by key.  Returns ``None`` if not found."""
        try:
            row = self._db.sqlite.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row is not None else None
        except Exception as exc:
            self._logger.warning("Failed to read app_settings[%s]: %s", key, exc)
            return None

    def set(self, key: str, value: str) -> bool:
        """Upsert a setting value.  Returns ``True`` on success."""
        try:
            with self._db.write_lock:
                self._db.sqlite.execute(
                    """
                    INSERT INTO app_settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value      = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, value),
                )
                self._db.sqlite.commit()
            self._logger.info("app_settings[%s] updated.", key)
            return True
        except Exception as exc:
            self._logger.error("Failed to write app_settings[%s]: %s", key, exc)
            return False

    # ------------------------------------------------------------------
    # Typed convenience — SharePoint root path
    # ------------------------------------------------------------------

    def get_sharepoint_root(self) -> Optional[str]:
        """Return the stored SharePoint root path, or ``None``."""
        return self.get(_KEY_SHAREPOINT_ROOT)

    def set_sharepoint_root(self, path: str) -> bool:
        """Persist the user-configured SharePoint root path."""
        return self.set(_KEY_SHAREPOINT_ROOT, path)
