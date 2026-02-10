"""
User Repository.

Handles all user data access via Supabase (primary) and SQLite (offline cache).
Replaces legacy db.session.get(User, pk), User.query, db.session.add/commit patterns.
"""

from __future__ import annotations

from app.logger import StructuredLogger
from typing import Optional

from app.database import DatabaseManager
from app.models.user import User
from app.models.enums import UserRole
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import normalize_keys, denormalize_keys


class UserRepository(BaseRepository):
    """Data access layer for User entities."""

    TABLE = "profiles"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)
        self._ensure_sqlite_table()

    def _ensure_sqlite_table(self) -> None:
        """Create the local SQLite cache table if it doesn't exist."""
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'SALES'
            )
            """
        )
        self.sqlite.commit()

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Fetch a user by primary key. Tries Supabase first, falls back to SQLite."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            if response.data:
                user = User(**normalize_keys(response.data))
                self._cache_to_sqlite(user)
                return user
        except Exception as exc:
            self._logger.warning("Supabase unavailable for user lookup: %s", exc)

        # Offline fallback
        row = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE} WHERE id = ?", (user_id,)
        ).fetchone()
        if row:
            return User(**dict(row))
        return None

    def get_by_full_name(self, full_name: str) -> Optional[User]:
        """Fetch a user by full_name. Tries Supabase first, falls back to SQLite."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("full_name", full_name)
                .maybe_single()
                .execute()
            )
            if response.data:
                user = User(**normalize_keys(response.data))
                self._cache_to_sqlite(user)
                return user
        except Exception as exc:
            self._logger.warning("Supabase unavailable for full_name lookup: %s", exc)

        row = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE} WHERE full_name = ?", (full_name,)
        ).fetchone()
        if row:
            return User(**dict(row))
        return None

    def get_all(self) -> list[User]:
        """Fetch all users. Excludes sensitive fields (password_hash)."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("id, email, full_name, role")
                .execute()
            )
            users = [User(**normalize_keys(row)) for row in response.data]
            for user in users:
                self._cache_to_sqlite(user)
            return users
        except Exception as exc:
            self._logger.warning("Supabase unavailable for get_all users: %s", exc)

        rows = self.sqlite.execute(
            f"SELECT id, email, full_name, role FROM {self.TABLE}"
        ).fetchall()
        return [User(**dict(row)) for row in rows]

    def upsert(self, user: User) -> User:
        """Insert or update a user. Writes to Supabase and caches to SQLite."""
        data = denormalize_keys(user.model_dump())
        try:
            response = (
                self.supabase.table(self.TABLE)
                .upsert(data)
                .execute()
            )
            result = User(**normalize_keys(response.data[0]))
            self._cache_to_sqlite(result)
            self._logger.info("User upserted: %s", result.id)
            return result
        except Exception as exc:
            self._logger.error("Failed to upsert user to Supabase: %s", exc)
            # Write to SQLite as pending sync
            self._cache_to_sqlite(user)
            self._queue_pending_sync("upsert", user.id, user.model_dump())
            return user

    def update_role(self, user_id: str, new_role: UserRole) -> Optional[User]:
        """Update a user's role. Returns updated user or None if not found."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .update({"role": str(new_role)})
                .eq("id", user_id)
                .execute()
            )
            if response.data:
                user = User(**normalize_keys(response.data[0]))
                self._cache_to_sqlite(user)
                return user
        except Exception as exc:
            self._logger.error("Failed to update role in Supabase: %s", exc)
            # Update SQLite as fallback
            self.sqlite.execute(
                f"UPDATE {self.TABLE} SET role = ? WHERE id = ?",
                (str(new_role), user_id),
            )
            self.sqlite.commit()
            self._queue_pending_sync("update_role", user_id, {"role": str(new_role)})

        return self.get_by_id(user_id)

    def _cache_to_sqlite(self, user: User) -> None:
        """Write user to local SQLite cache for offline access."""
        self.sqlite.execute(
            f"""
            INSERT INTO {self.TABLE} (id, email, full_name, role)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                full_name = excluded.full_name,
                role = excluded.role
            """,
            (user.id, user.email, user.full_name, str(user.role)),
        )
        self.sqlite.commit()

