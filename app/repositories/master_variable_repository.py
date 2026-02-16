"""
Master Variable Repository.

Handles all master variable data access (exchange rates, cost of capital, etc.).
Variables are append-only (historical records preserved for audit trail).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.master_variable import MasterVariable
from app.repositories.base_repository import BaseRepository


class MasterVariableRepository(BaseRepository):
    """Data access layer for MasterVariable entities."""

    TABLE = "master_variables"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)

    def get_all(self, category: Optional[str] = None) -> list[MasterVariable]:
        """Fetch all master variables, optionally filtered by category."""
        def _supabase() -> list[MasterVariable]:
            query = (
                self.supabase.table(self.TABLE)
                .select("*")
                .order("date_recorded", desc=True)
            )
            if category:
                query = query.eq("category", category)
            response = query.execute()
            return [MasterVariable(**row) for row in response.data]

        def _sqlite() -> list[MasterVariable]:
            sql = f"SELECT * FROM {self.TABLE}"
            params: list[str] = []
            if category:
                sql += " WHERE category = ?"
                params.append(category)
            sql += " ORDER BY date_recorded DESC"
            rows = self.sqlite.execute(sql, params).fetchall()
            return [MasterVariable(**dict(row)) for row in rows]

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=list,
            operation_name="get_all (master_variables)",
        )

    def get_latest(self, variable_names: list[str]) -> dict[str, Optional[Decimal]]:
        """
        Get the most recent value for each variable name.

        Returns a dict mapping variable_name -> latest value (or None if not found).
        This replaces the legacy SQLAlchemy subquery + join pattern.
        """
        def _make_default() -> dict[str, Optional[Decimal]]:
            return {name: None for name in variable_names}

        def _supabase() -> dict[str, Optional[Decimal]]:
            response = (
                self.supabase.table(self.TABLE)
                .select("variable_name, variable_value, date_recorded")
                .in_("variable_name", variable_names)
                .order("date_recorded", desc=True)
                .execute()
            )
            result = _make_default()
            seen: set[str] = set()
            for row in response.data:
                name = str(row.get("variable_name", ""))
                if name not in seen and name in result:
                    result[name] = Decimal(str(row.get("variable_value", 0)))
                    seen.add(name)
            return result

        def _sqlite() -> Optional[dict[str, Optional[Decimal]]]:
            if not variable_names:
                return None
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
            if not rows:
                return None
            result = _make_default()
            for row in rows:
                row_dict = dict(row)
                result[row_dict["variable_name"]] = Decimal(str(row_dict["variable_value"]))
            return result

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=_make_default,
            operation_name="get_latest (master_variables)",
        )

    def create(self, variable: MasterVariable) -> MasterVariable:
        """
        Insert a new master variable record (append-only for audit trail).
        Writes to Supabase and caches to SQLite.
        """
        data = variable.model_dump(exclude={"id"})

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(data)
                .execute()
            )
            created = MasterVariable(**response.data[0])
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
        # Convert Decimal to float for SQLite REAL column
        variable_value_for_sqlite = (
            float(variable.variable_value)
            if isinstance(variable.variable_value, Decimal)
            else variable.variable_value
        )
        self.sqlite.execute(
            f"""
            INSERT INTO {self.TABLE}
                (variable_name, variable_value, category, user_id, comment, date_recorded)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                variable.variable_name,
                variable_value_for_sqlite,
                variable.category,
                variable.user_id,
                variable.comment,
                variable.date_recorded.isoformat(),
            ),
        )
        self._commit()

