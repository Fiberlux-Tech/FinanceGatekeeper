"""
Fixed Cost Repository.

Handles data access for FixedCost line items belonging to transactions.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from app.logger import StructuredLogger
from app.database import DatabaseManager
from app.models.fixed_cost import FixedCost
from app.repositories.base_repository import BaseRepository


class FixedCostRepository(BaseRepository):
    """Data access layer for FixedCost entities."""

    TABLE = "fixed_costs"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)

    def get_by_id(self, item_id: int) -> Optional[FixedCost]:
        """Fetch a single fixed cost by its primary key.

        Tries Supabase first, falls back to SQLite.

        Args:
            item_id: The integer primary key of the fixed cost row.

        Returns:
            The FixedCost if found, or None.
        """
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", item_id)
                .maybe_single()
                .execute()
            )
            if response.data:
                return FixedCost(**response.data)
        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for fixed cost lookup by id: %s", exc
            )

        try:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (item_id,)
            ).fetchone()
            if row:
                return FixedCost(**dict(row))
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for get_by_id (fixed_costs): %s",
                sqlite_exc,
            )
        return None

    def get_by_transaction(self, transaction_id: str) -> list[FixedCost]:
        """Fetch all fixed costs for a transaction."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            return [FixedCost(**row) for row in response.data]
        except Exception as exc:
            self._logger.warning("Supabase unavailable for fixed costs: %s", exc)

        try:
            rows = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchall()
            return [FixedCost(**dict(row)) for row in rows]
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for get_by_transaction (fixed_costs): %s",
                sqlite_exc,
            )
            return []

    def replace_for_transaction(
        self, transaction_id: str, costs: list[FixedCost]
    ) -> list[FixedCost]:
        """Replace all fixed costs for a transaction.

        Uses a compensating-transaction pattern (C-6 fix): if the INSERT
        fails after the DELETE, the old rows are re-inserted so that
        data is never permanently lost.
        """
        created: list[FixedCost] = []

        try:
            # Snapshot existing rows for compensating rollback
            old_response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            old_rows: list[dict[str, object]] = old_response.data or []

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
                    rows_to_insert.append(data)
                try:
                    response = (
                        self.supabase.table(self.TABLE)
                        .insert(rows_to_insert)
                        .execute()
                    )
                    created = [
                        FixedCost(**row) for row in response.data
                    ]
                except Exception as insert_exc:
                    # Compensating rollback: re-insert old rows
                    self._logger.error(
                        "INSERT failed after DELETE for fixed_costs "
                        "(transaction %s); rolling back: %s",
                        transaction_id,
                        insert_exc,
                    )
                    if old_rows:
                        # Strip server-generated IDs so Supabase auto-assigns
                        restore_rows = [
                            {k: v for k, v in row.items() if k != "id"}
                            for row in old_rows
                        ]
                        self.supabase.table(self.TABLE).insert(restore_rows).execute()
                    raise

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
            rows_to_insert.append(data)

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(rows_to_insert)
                .execute()
            )
            created = [FixedCost(**row) for row in response.data]
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
        self._commit()

