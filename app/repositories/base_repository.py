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
from typing import Callable, Optional, TypeVar, Union

from supabase import Client as SupabaseClient

from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.utils.string_helpers import JsonValue

T = TypeVar("T")


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

    def _execute_with_fallback(
        self,
        supabase_op: Callable[[], Optional[T]],
        sqlite_op: Callable[[], Optional[T]],
        default_factory: Callable[[], T],
        *,
        operation_name: str,
        on_supabase_success: Optional[Callable[[T], None]] = None,
    ) -> T:
        """Execute a read operation with Supabase-first, SQLite-fallback semantics.

        Encapsulates the offline-first read pattern used throughout the
        repository layer.  NOT intended for write paths, compensating
        transactions, or operations that interact with the sync queue.

        Execution order:
        1. Call ``supabase_op()``.  If it returns a non-``None`` value,
           optionally invoke ``on_supabase_success``, then return.
        2. Call ``sqlite_op()``.  If it returns a non-``None`` value, return.
        3. Return ``default_factory()``.

        Parameters
        ----------
        supabase_op:
            Zero-argument callable that performs the Supabase query.
            Returns the result or ``None`` if not found.
        sqlite_op:
            Zero-argument callable that performs the SQLite query.
            Returns the result or ``None`` if not found.
        default_factory:
            Zero-argument callable producing the typed default when both
            sources fail or return ``None``.
        operation_name:
            Human-readable label for log messages, e.g.
            ``"get_by_id (profiles)"``.
        on_supabase_success:
            Optional callback invoked with the Supabase result before it
            is returned.  Designed for cache-warming side effects.
            Exceptions are logged as warnings but never mask the result.
        """
        try:
            result = supabase_op()
            if result is not None:
                if on_supabase_success is not None:
                    try:
                        on_supabase_success(result)
                    except Exception as cache_exc:
                        self._logger.warning(
                            "Post-Supabase callback failed for %s: %s",
                            operation_name,
                            cache_exc,
                        )
                return result
        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for %s: %s", operation_name, exc
            )

        try:
            result = sqlite_op()
            if result is not None:
                return result
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for %s: %s",
                operation_name,
                sqlite_exc,
            )

        return default_factory()

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
