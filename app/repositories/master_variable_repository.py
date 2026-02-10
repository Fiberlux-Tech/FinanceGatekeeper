"""
Master Variable Repository.

Handles all master variable data access (exchange rates, cost of capital, etc.).
Variables are append-only (historical records preserved for audit trail).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.database import DatabaseManager
from app.models.master_variable import MasterVariable
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import normalize_keys, denormalize_keys


class MasterVariableRepository(BaseRepository):
    """Data access layer for MasterVariable entities."""

    TABLE = "master_variables"

    def __init__(self, db: DatabaseManager, logger: logging.Logger) -> None:
        super().__init__(db, logger)
        self._ensure_sqlite_table()

    def _ensure_sqlite_table(self) -> None:
        """Create the local SQLite cache table if it doesn't exist."""
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                variable_name TEXT NOT NULL,
                variable_value REAL NOT NULL,
                category TEXT NOT NULL,
                user_id TEXT NOT NULL,
                comment TEXT,
                date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.sqlite.commit()

    def get_all(self, category: Optional[str] = None) -> list[MasterVariable]:
        """Fetch all master variables, optionally filtered by category."""
        try:
            query = (
                self.supabase.table(self.TABLE)
                .select("*")
                .order("date_recorded", desc=True)
            )
            if category:
                query = query.eq("category", category)
            response = query.execute()
            variables = [
                MasterVariable(**normalize_keys(row)) for row in response.data
            ]
            return variables
        except Exception as exc:
            self._logger.warning("Supabase unavailable for get_all variables: %s", exc)

        # SQLite fallback
        sql = f"SELECT * FROM {self.TABLE}"
        params: list[str] = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY date_recorded DESC"

        rows = self.sqlite.execute(sql, params).fetchall()
        return [MasterVariable(**dict(row)) for row in rows]

    def get_latest(self, variable_names: list[str]) -> dict[str, Optional[float]]:
        """
        Get the most recent value for each variable name.

        Returns a dict mapping variable_name -> latest value (or None if not found).
        This replaces the legacy SQLAlchemy subquery + join pattern.
        """
        result: dict[str, Optional[float]] = {name: None for name in variable_names}

        try:
            # Supabase: fetch all records for these variable names, ordered by date desc
            response = (
                self.supabase.table(self.TABLE)
                .select("variable_name, variable_value, date_recorded")
                .in_("variable_name", variable_names)
                .order("date_recorded", desc=True)
                .execute()
            )
            # Group by variable_name and take the first (most recent) for each
            seen: set[str] = set()
            for row in response.data:
                normalized = normalize_keys(row)
                name = str(normalized.get("variable_name", ""))
                if name not in seen and name in result:
                    result[name] = float(normalized.get("variable_value", 0))
                    seen.add(name)
            return result
        except Exception as exc:
            self._logger.warning("Supabase unavailable for get_latest: %s", exc)

        # SQLite fallback: use GROUP BY with MAX(date_recorded)
        if not variable_names:
            return result

        placeholders = ", ".join("?" for _ in variable_names)
        rows = self.sqlite.execute(
            f"""
            SELECT mv.variable_name, mv.variable_value
            FROM {self.TABLE} mv
            INNER JOIN (
                SELECT variable_name, MAX(date_recorded) AS max_date
                FROM {self.TABLE}
                WHERE variable_name IN ({placeholders})
                GROUP BY variable_name
            ) latest ON mv.variable_name = latest.variable_name
                    AND mv.date_recorded = latest.max_date
            """,
            variable_names,
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            result[row_dict["variable_name"]] = float(row_dict["variable_value"])

        return result

    def create(self, variable: MasterVariable) -> MasterVariable:
        """
        Insert a new master variable record (append-only for audit trail).
        Writes to Supabase and caches to SQLite.
        """
        data = denormalize_keys(variable.model_dump(exclude={"id"}))

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(data)
                .execute()
            )
            created = MasterVariable(**normalize_keys(response.data[0]))
            self._cache_to_sqlite(created)
            self._logger.info(
                "Master variable created: %s = %s",
                created.variable_name,
                created.variable_value,
            )
            return created
        except Exception as exc:
            self._logger.error("Failed to create master variable in Supabase: %s", exc)
            # Write to SQLite and queue for sync
            self._cache_to_sqlite(variable)
            self._queue_pending_sync(
                "insert", variable.variable_name, variable.model_dump(exclude={"id"})
            )
            return variable

    def _cache_to_sqlite(self, variable: MasterVariable) -> None:
        """Write variable record to local SQLite cache."""
        self.sqlite.execute(
            f"""
            INSERT INTO {self.TABLE}
                (variable_name, variable_value, category, user_id, comment, date_recorded)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                variable.variable_name,
                variable.variable_value,
                variable.category,
                variable.user_id,
                variable.comment,
                variable.date_recorded.isoformat(),
            ),
        )
        self.sqlite.commit()

