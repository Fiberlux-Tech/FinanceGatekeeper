"""
Recurring Service Repository.

Handles data access for RecurringService line items belonging to transactions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.logger import StructuredLogger
from app.database import DatabaseManager
from app.models.recurring_service import RecurringService
from app.repositories.base_repository import BaseRepository


class RecurringServiceRepository(BaseRepository):
    """Data access layer for RecurringService entities."""

    TABLE = "recurring_services"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)

    def get_by_id(self, item_id: int) -> Optional[RecurringService]:
        """Fetch a single recurring service by its primary key.

        Tries Supabase first, falls back to SQLite.

        Args:
            item_id: The integer primary key of the recurring service row.

        Returns:
            The RecurringService if found, or None.
        """
        def _supabase() -> Optional[RecurringService]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", item_id)
                .maybe_single()
                .execute()
            )
            return RecurringService(**response.data) if response.data else None

        def _sqlite() -> Optional[RecurringService]:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (item_id,)
            ).fetchone()
            return RecurringService(**dict(row)) if row else None

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=lambda: None,
            operation_name="get_by_id (recurring_services)",
        )

    def get_by_transaction(self, transaction_id: str) -> list[RecurringService]:
        """Fetch all recurring services for a transaction."""
        def _supabase() -> list[RecurringService]:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            return [RecurringService(**row) for row in response.data]

        def _sqlite() -> list[RecurringService]:
            rows = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchall()
            return [RecurringService(**dict(row)) for row in rows]

        return self._execute_with_fallback(
            supabase_op=_supabase,
            sqlite_op=_sqlite,
            default_factory=list,
            operation_name="get_by_transaction (recurring_services)",
        )

    def replace_for_transaction(
        self, transaction_id: str, services: list[RecurringService]
    ) -> list[RecurringService]:
        """Replace all recurring services for a transaction.

        Uses a compensating-transaction pattern (C-6 fix): if the INSERT
        fails after the DELETE, the old rows are re-inserted so that
        data is never permanently lost.
        """
        created: list[RecurringService] = []

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
            if services:
                rows_to_insert = []
                for svc in services:
                    data = svc.model_dump(exclude={"id"})
                    data["transaction_id"] = transaction_id
                    rows_to_insert.append(data)
                try:
                    response = (
                        self.supabase.table(self.TABLE)
                        .insert(rows_to_insert)
                        .execute()
                    )
                    created = [
                        RecurringService(**row)
                        for row in response.data
                    ]
                except Exception as insert_exc:
                    # Compensating rollback: re-insert old rows
                    self._logger.error(
                        "INSERT failed after DELETE for recurring_services "
                        "(transaction %s); rolling back: %s",
                        transaction_id,
                        insert_exc,
                    )
                    if old_rows:
                        restore_rows = [
                            {k: v for k, v in row.items() if k != "id"}
                            for row in old_rows
                        ]
                        self.supabase.table(self.TABLE).insert(restore_rows).execute()
                    raise

            # Sync to SQLite
            self._replace_in_sqlite(transaction_id, created or services)
            return created or services

        except Exception as exc:
            self._logger.error(
                "Failed to replace recurring services in Supabase: %s", exc
            )
            self._replace_in_sqlite(transaction_id, services)
            self._queue_pending_sync(
                "replace", transaction_id, [s.model_dump(exclude={"id"}) for s in services]
            )
            return services

    def create_batch(
        self, transaction_id: str, services: list[RecurringService]
    ) -> list[RecurringService]:
        """Insert a batch of recurring services for a new transaction."""
        if not services:
            return []

        rows_to_insert = []
        for svc in services:
            data = svc.model_dump(exclude={"id"})
            data["transaction_id"] = transaction_id
            rows_to_insert.append(data)

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(rows_to_insert)
                .execute()
            )
            created = [
                RecurringService(**row) for row in response.data
            ]
            self._replace_in_sqlite(transaction_id, created)
            return created
        except Exception as exc:
            self._logger.error(
                "Failed to create recurring services in Supabase: %s", exc
            )
            self._replace_in_sqlite(transaction_id, services)
            self._queue_pending_sync(
                "replace", transaction_id, [s.model_dump(exclude={"id"}) for s in services]
            )
            return services

    def _replace_in_sqlite(
        self, transaction_id: str, services: list[RecurringService]
    ) -> None:
        """Replace recurring services in SQLite cache."""
        self.sqlite.execute(
            f"DELETE FROM {self.TABLE} WHERE transaction_id = ?",
            (transaction_id,),
        )
        for svc in services:
            self.sqlite.execute(
                f"""
                INSERT INTO {self.TABLE}
                    (transaction_id, tipo_servicio, nota, ubicacion, quantity,
                     price_original, price_currency, price_pen,
                     cost_unit_1_original, cost_unit_2_original,
                     cost_unit_currency, cost_unit_1_pen, cost_unit_2_pen,
                     proveedor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    svc.tipo_servicio,
                    svc.nota,
                    svc.ubicacion,
                    float(svc.quantity) if isinstance(svc.quantity, Decimal) else svc.quantity,
                    float(svc.price_original) if isinstance(svc.price_original, Decimal) else svc.price_original,
                    str(svc.price_currency),
                    float(svc.price_pen) if isinstance(svc.price_pen, Decimal) else svc.price_pen,
                    float(svc.cost_unit_1_original) if isinstance(svc.cost_unit_1_original, Decimal) else svc.cost_unit_1_original,
                    float(svc.cost_unit_2_original) if isinstance(svc.cost_unit_2_original, Decimal) else svc.cost_unit_2_original,
                    str(svc.cost_unit_currency),
                    float(svc.cost_unit_1_pen) if isinstance(svc.cost_unit_1_pen, Decimal) else svc.cost_unit_1_pen,
                    float(svc.cost_unit_2_pen) if isinstance(svc.cost_unit_2_pen, Decimal) else svc.cost_unit_2_pen,
                    svc.proveedor,
                ),
            )
        self._commit()

