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
from app.logger import StructuredLogger
import sqlite3
from typing import Optional

from supabase import Client as SupabaseClient

from app.database import DatabaseManager


class BaseRepository:
    """Base class for all repositories. Receives dependencies via __init__."""

    TABLE: str = ""

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        self._db = db
        self._logger = logger
        self._ensure_sync_queue_table()

    @property
    def supabase(self) -> SupabaseClient:
        """Returns the Supabase client for cloud operations."""
        return self._db.supabase

    @property
    def sqlite(self) -> sqlite3.Connection:
        """Returns the SQLite connection for local cache operations."""
        return self._db.sqlite

    def _ensure_sync_queue_table(self) -> None:
        """Create the sync_queue table if it doesn't exist (shared across all repos)."""
        self.sqlite.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                operation TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.sqlite.commit()

    def _queue_pending_sync(
        self, operation: str, entity_id: str, payload: object
    ) -> None:
        """
        Mark an operation for background sync when connectivity is restored.

        Args:
            operation: The type of operation (insert, update, upsert, replace, etc.).
            entity_id: The ID of the affected entity.
            payload: The data payload (will be JSON-serialized).
        """
        self.sqlite.execute(
            """
            INSERT INTO sync_queue (table_name, operation, entity_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (self.TABLE, operation, entity_id, json.dumps(payload, default=str)),
        )
        self.sqlite.commit()
        self._logger.info(
            "Queued pending sync: %s %s/%s", operation, self.TABLE, entity_id
        )
