"""
Base Repository.

Provides shared infrastructure for all repositories:
- DatabaseManager reference (Supabase + SQLite)
- Logger reference
- Convenience properties for accessing clients
- Sync queue management for offline-first writes
"""

from __future__ import annotations

import json
import sqlite3
from typing import Union

from supabase import Client as SupabaseClient

from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.utils.string_helpers import JsonValue


class BaseRepository:
    """Base class for all repositories. Receives dependencies via __init__."""

    TABLE: str = ""

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        self._db = db
        self._logger = logger

    @property
    def supabase(self) -> SupabaseClient:
        """Returns the Supabase client for cloud operations."""
        return self._db.supabase

    @property
    def sqlite(self) -> sqlite3.Connection:
        """Returns the SQLite connection for local cache operations."""
        return self._db.sqlite

    def _commit(self) -> None:
        """Commit the SQLite transaction unless a batch is active.

        When :meth:`DatabaseManager.batch_write` is active, this is a
        no-op — the batch context manager issues a single commit (or
        rollback) when the ``with`` block exits.  Outside a batch,
        commits happen immediately as before.

        All repository code should call ``self._commit()`` instead of
        ``self.sqlite.commit()`` so that batch writes work transparently.
        """
        if not self._db.in_batch:
            self.sqlite.commit()

    def _queue_pending_sync(
        self,
        operation: str,
        entity_id: str,
        payload: Union[dict[str, JsonValue], list[dict[str, JsonValue]]],
    ) -> None:
        """
        Mark an operation for background sync when connectivity is restored.

        Args:
            operation: The type of operation (insert, update, upsert, replace, etc.).
            entity_id: The ID of the affected entity.
            payload: The data payload (will be JSON-serialized).  Must be a
                     dict or list of dicts with JSON-safe values.
        """
        try:
            self.sqlite.execute(
                """
                INSERT INTO sync_queue (table_name, operation, entity_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (self.TABLE, operation, entity_id, json.dumps(payload, default=str)),
            )
            self._commit()
            self._logger.info(
                "Queued pending sync: %s %s/%s", operation, self.TABLE, entity_id
            )
        except sqlite3.Error as exc:
            self._logger.warning(
                "Failed to queue pending sync for %s/%s: %s",
                self.TABLE,
                entity_id,
                exc,
            )

    def _mark_synced(self, queue_id: int) -> None:
        """Transition a sync-queue row from ``pending`` to ``synced``.

        Parameters
        ----------
        queue_id:
            The ``id`` (primary key) of the ``sync_queue`` row.
        """
        try:
            self.sqlite.execute(
                """
                UPDATE sync_queue
                SET status = 'synced', attempted_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (queue_id,),
            )
            self._commit()
        except sqlite3.Error as exc:
            self._logger.warning(
                "Failed to mark sync_queue row %d as synced: %s",
                queue_id,
                exc,
            )

    def _mark_failed(self, queue_id: int, error_message: str) -> None:
        """Transition a sync-queue row from ``pending`` to ``failed``.

        Parameters
        ----------
        queue_id:
            The ``id`` (primary key) of the ``sync_queue`` row.
        error_message:
            Human-readable description of the failure (stored in the
            ``error_message`` column for diagnostics).
        """
        try:
            self.sqlite.execute(
                """
                UPDATE sync_queue
                SET status = 'failed',
                    attempted_at = CURRENT_TIMESTAMP,
                    error_message = ?
                WHERE id = ?
                """,
                (error_message, queue_id),
            )
            self._commit()
        except sqlite3.Error as exc:
            self._logger.warning(
                "Failed to mark sync_queue row %d as failed: %s",
                queue_id,
                exc,
            )
