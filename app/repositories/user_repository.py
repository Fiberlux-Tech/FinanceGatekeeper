"""
User Repository.

Handles all user data access via Supabase (primary) and SQLite (offline cache).
Replaces legacy db.session.get(User, pk), User.query, db.session.add/commit patterns.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.base_repository import BaseRepository


class UserRepository(BaseRepository):
    """Data access layer for User entities.

    **No ``delete()`` method — by design.**

    Users are never hard-deleted.  The audit trail (CLAUDE.md Section 5)
    requires that every state change is traceable to a real identity.
    Removing a user row would orphan transaction records, approval logs,
    and commission history.

    Instead, use :meth:`deactivate` to revoke access.  A deactivated
    user's role is set to ``UserRole.DEACTIVATED``, which prevents login
    while preserving all historical associations.
    """

    TABLE = "profiles"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Fetch a user by primary key. Tries Supabase first, falls back to SQLite."""
        def _supabase() -> Optional[User]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            return User(**response.data) if response.data else None

        def _sqlite() -> Optional[User]:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (user_id,)
            ).fetchone()
            return User(**dict(row)) if row else None

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=lambda: None,
            operation_name="get_by_id (profiles)",
            on_supabase_success=self._cache_to_sqlite,
        )

    def get_by_email(self, email: str) -> Optional[User]:
        """Fetch a user by email address.

        Email is the primary identity per CLAUDE.md Section 7.
        Tries Supabase first, falls back to SQLite.

        Args:
            email: The user's email address (case-insensitive lookup).

        Returns:
            The User if found, or None.
        """
        normalized_email = email.strip().lower()

        def _supabase() -> Optional[User]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("email", normalized_email)
                .maybe_single()
                .execute()
            )
            return User(**response.data) if response.data else None

        def _sqlite() -> Optional[User]:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE email = ?", (normalized_email,)
            ).fetchone()
            return User(**dict(row)) if row else None

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=lambda: None,
            operation_name="get_by_email (profiles)",
            on_supabase_success=self._cache_to_sqlite,
        )

    def get_by_full_name(self, full_name: str) -> Optional[User]:
        """Fetch a user by full_name. Tries Supabase first, falls back to SQLite."""
        def _supabase() -> Optional[User]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("full_name", full_name)
                .maybe_single()
                .execute()
            )
            return User(**response.data) if response.data else None

        def _sqlite() -> Optional[User]:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE full_name = ?", (full_name,)
            ).fetchone()
            return User(**dict(row)) if row else None

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=lambda: None,
            operation_name="get_by_full_name (profiles)",
            on_supabase_success=self._cache_to_sqlite,
        )

    def get_all(self) -> list[User]:
        """Fetch all users. Excludes sensitive fields (password_hash)."""
        def _supabase() -> list[User]:
            response = (
                self.supabase.table(self.TABLE)
                .select("id, email, full_name, role, created_at, updated_at")
                .execute()
            )
            return [User(**row) for row in response.data]

        def _sqlite() -> list[User]:
            rows = self.sqlite.execute(
                f"SELECT id, email, full_name, role, created_at, updated_at "
                f"FROM {self.TABLE}"
            ).fetchall()
            return [User(**dict(row)) for row in rows]

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=list,
            operation_name="get_all (profiles)",
            on_supabase_success=lambda users: [self._cache_to_sqlite(u) for u in users],
        )

    def upsert(self, user: User) -> User:
        """Insert or update a user. Writes to Supabase and caches to SQLite."""
        data = user.model_dump()
        try:
            response = (
                self.supabase.table(self.TABLE)
                .upsert(data)
                .execute()
            )
            result = User(**response.data[0])
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
                user = User(**response.data[0])
                self._cache_to_sqlite(user)
                return user
        except Exception as exc:
            self._logger.error("Failed to update role in Supabase: %s", exc)
            # Update SQLite as fallback
            self.sqlite.execute(
                f"UPDATE {self.TABLE} SET role = ? WHERE id = ?",
                (str(new_role), user_id),
            )
            self._commit()
            self._queue_pending_sync("update_role", user_id, {"role": str(new_role)})

        return self.get_by_id(user_id)

    def deactivate(self, user_id: str) -> bool:
        """Soft-delete a user by setting their role to ``DEACTIVATED``.

        This is the only supported removal mechanism.  Hard deletion is
        intentionally forbidden to preserve audit trail integrity
        (CLAUDE.md Section 5).  A deactivated user retains their row in
        ``profiles`` — their email, full_name, and historical associations
        remain intact — but they can no longer authenticate.

        Args:
            user_id: The UUID of the user to deactivate.

        Returns:
            ``True`` if the user was found and deactivated (or was already
            deactivated), ``False`` if the user does not exist.
        """
        existing = self.get_by_id(user_id)
        if existing is None:
            self._logger.warning(
                "Cannot deactivate user %s: not found.", user_id,
            )
            return False

        if existing.role == UserRole.DEACTIVATED:
            self._logger.info(
                "User %s is already deactivated — no-op.", user_id,
            )
            return True

        result = self.update_role(user_id, UserRole.DEACTIVATED)
        if result is not None and result.role == UserRole.DEACTIVATED:
            self._logger.info("User deactivated: %s", user_id)
            return True

        self._logger.error(
            "Deactivation may have failed for user %s — role update "
            "did not confirm DEACTIVATED status.",
            user_id,
        )
        return False

    def _cache_to_sqlite(self, user: User) -> None:
        """Write user to local SQLite cache for offline access.

        Caches all six schema columns (id, email, full_name, role,
        created_at, updated_at) so that offline reads return a fully
        populated ``User`` model.

        Exceptions are logged but not raised so that a local cache
        failure never masks a successful Supabase operation (M-33).
        """
        created_at_str: str | None = (
            user.created_at.isoformat() if user.created_at else None
        )
        updated_at_str: str | None = (
            user.updated_at.isoformat() if user.updated_at else None
        )
        try:
            self.sqlite.execute(
                f"""
                INSERT INTO {self.TABLE}
                    (id, email, full_name, role, created_at, updated_at)
                VALUES (?, ?, ?, ?,
                        COALESCE(?, CURRENT_TIMESTAMP),
                        COALESCE(?, CURRENT_TIMESTAMP))
                ON CONFLICT(id) DO UPDATE SET
                    email      = excluded.email,
                    full_name  = excluded.full_name,
                    role       = excluded.role,
                    updated_at = COALESCE(?, CURRENT_TIMESTAMP)
                """,
                (
                    user.id,
                    user.email,
                    user.full_name,
                    str(user.role),
                    created_at_str,
                    updated_at_str,
                    updated_at_str,
                ),
            )
            self._commit()
        except sqlite3.Error as exc:
            self._logger.warning(
                "Failed to cache user %s to SQLite (non-fatal): %s",
                user.id,
                exc,
            )

