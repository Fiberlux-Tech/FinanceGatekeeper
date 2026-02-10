"""
Fixed Cost Repository.

Handles data access for FixedCost line items belonging to transactions.
"""

from __future__ import annotations

from app.logger import StructuredLogger
from typing import Optional

from app.database import DatabaseManager
from app.models.fixed_cost import FixedCost
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import normalize_keys, denormalize_keys


class FixedCostRepository(BaseRepository):
    """Data access layer for FixedCost entities."""

    TABLE = "fixed_costs"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)
        self._ensure_sqlite_table()

    def _ensure_sqlite_table(self) -> None:
        """Create the local SQLite cache table if it doesn't exist."""
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT NOT NULL,
                categoria TEXT,
                tipo_servicio TEXT,
                ticket TEXT,
                ubicacion TEXT,
                cantidad REAL,
                costo_unitario_original REAL,
                costo_unitario_currency TEXT DEFAULT 'USD',
                costo_unitario_pen REAL,
                periodo_inicio INTEGER DEFAULT 0,
                duracion_meses INTEGER DEFAULT 1
            )
            """
        )
        self.sqlite.commit()

    def get_by_transaction(self, transaction_id: str) -> list[FixedCost]:
        """Fetch all fixed costs for a transaction."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            return [FixedCost(**normalize_keys(row)) for row in response.data]
        except Exception as exc:
            self._logger.warning("Supabase unavailable for fixed costs: %s", exc)

        rows = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE} WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchall()
        return [FixedCost(**dict(row)) for row in rows]

    def replace_for_transaction(
        self, transaction_id: str, costs: list[FixedCost]
    ) -> list[FixedCost]:
        """
        Replace all fixed costs for a transaction (atomic delete + insert).
        Used when recalculating or updating transaction content.
        """
        created: list[FixedCost] = []

        try:
            # Delete existing
            self.supabase.table(self.TABLE).delete().eq(
                "transaction_id", transaction_id
            ).execute()

            # Insert new
            if costs:
                rows_to_insert = []
                for cost in costs:
                    data = cost.model_dump(exclude={"id"})
                    data["transaction_id"] = transaction_id
                    rows_to_insert.append(denormalize_keys(data))
                response = (
                    self.supabase.table(self.TABLE)
                    .insert(rows_to_insert)
                    .execute()
                )
                created = [
                    FixedCost(**normalize_keys(row)) for row in response.data
                ]

            # Sync to SQLite
            self._replace_in_sqlite(transaction_id, created or costs)
            return created or costs

        except Exception as exc:
            self._logger.error(
                "Failed to replace fixed costs in Supabase: %s", exc
            )
            # SQLite fallback
            self._replace_in_sqlite(transaction_id, costs)
            self._queue_pending_sync(
                "replace", transaction_id, [c.model_dump(exclude={"id"}) for c in costs]
            )
            return costs

    def create_batch(
        self, transaction_id: str, costs: list[FixedCost]
    ) -> list[FixedCost]:
        """Insert a batch of fixed costs for a new transaction."""
        if not costs:
            return []

        rows_to_insert = []
        for cost in costs:
            data = cost.model_dump(exclude={"id"})
            data["transaction_id"] = transaction_id
            rows_to_insert.append(denormalize_keys(data))

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(rows_to_insert)
                .execute()
            )
            created = [FixedCost(**normalize_keys(row)) for row in response.data]
            self._replace_in_sqlite(transaction_id, created)
            return created
        except Exception as exc:
            self._logger.error("Failed to create fixed costs in Supabase: %s", exc)
            self._replace_in_sqlite(transaction_id, costs)
            self._queue_pending_sync(
                "replace", transaction_id, [c.model_dump(exclude={"id"}) for c in costs]
            )
            return costs

    def _replace_in_sqlite(
        self, transaction_id: str, costs: list[FixedCost]
    ) -> None:
        """Replace fixed costs in SQLite cache."""
        self.sqlite.execute(
            f"DELETE FROM {self.TABLE} WHERE transaction_id = ?",
            (transaction_id,),
        )
        for cost in costs:
            self.sqlite.execute(
                f"""
                INSERT INTO {self.TABLE}
                    (transaction_id, categoria, tipo_servicio, ticket, ubicacion,
                     cantidad, costo_unitario_original, costo_unitario_currency,
                     costo_unitario_pen, periodo_inicio, duracion_meses)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    cost.categoria,
                    cost.tipo_servicio,
                    cost.ticket,
                    cost.ubicacion,
                    cost.cantidad,
                    cost.costo_unitario_original,
                    str(cost.costo_unitario_currency),
                    cost.costo_unitario_pen,
                    cost.periodo_inicio,
                    cost.duracion_meses,
                ),
            )
        self.sqlite.commit()

