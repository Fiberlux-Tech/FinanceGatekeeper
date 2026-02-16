"""
Fixed Cost Repository.

Handles data access for FixedCost line items belonging to transactions.
"""

from __future__ import annotations

from decimal import Decimal
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
        def _supabase() -> Optional[FixedCost]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", item_id)
                .maybe_single()
                .execute()
            )
            return FixedCost(**response.data) if response.data else None

        def _sqlite() -> Optional[FixedCost]:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (item_id,)
            ).fetchone()
            return FixedCost(**dict(row)) if row else None

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=lambda: None,
            operation_name="get_by_id (fixed_costs)",
        )

    def get_by_transaction(self, transaction_id: str) -> list[FixedCost]:
        """Fetch all fixed costs for a transaction."""
        def _supabase() -> list[FixedCost]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            return [FixedCost(**row) for row in response.data]

        def _sqlite() -> list[FixedCost]:
            rows = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchall()
            return [FixedCost(**dict(row)) for row in rows]

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=list,
            operation_name="get_by_transaction (fixed_costs)",
        )

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
                    float(cost.cantidad) if isinstance(cost.cantidad, Decimal) else cost.cantidad,
                    float(cost.costo_unitario_original) if isinstance(cost.costo_unitario_original, Decimal) else cost.costo_unitario_original,
                    str(cost.costo_unitario_currency),
                    float(cost.costo_unitario_pen) if isinstance(cost.costo_unitario_pen, Decimal) else cost.costo_unitario_pen,
                    float(cost.periodo_inicio) if isinstance(cost.periodo_inicio, Decimal) else cost.periodo_inicio,
                    float(cost.duracion_meses) if isinstance(cost.duracion_meses, Decimal) else cost.duracion_meses,
                ),
            )
        self._commit()

