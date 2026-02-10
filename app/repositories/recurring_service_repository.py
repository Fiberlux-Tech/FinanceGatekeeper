"""
Recurring Service Repository.

Handles data access for RecurringService line items belonging to transactions.
"""

from __future__ import annotations

from app.logger import StructuredLogger
from typing import Optional

from app.database import DatabaseManager
from app.models.recurring_service import RecurringService
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import normalize_keys, denormalize_keys


class RecurringServiceRepository(BaseRepository):
    """Data access layer for RecurringService entities."""

    TABLE = "recurring_services"

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
                tipo_servicio TEXT,
                nota TEXT,
                ubicacion TEXT,
                quantity REAL,
                price_original REAL,
                price_currency TEXT DEFAULT 'PEN',
                price_pen REAL,
                cost_unit_1_original REAL,
                cost_unit_2_original REAL,
                cost_unit_currency TEXT DEFAULT 'USD',
                cost_unit_1_pen REAL,
                cost_unit_2_pen REAL,
                proveedor TEXT
            )
            """
        )
        self.sqlite.commit()

    def get_by_transaction(self, transaction_id: str) -> list[RecurringService]:
        """Fetch all recurring services for a transaction."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("transaction_id", transaction_id)
                .execute()
            )
            return [
                RecurringService(**normalize_keys(row)) for row in response.data
            ]
        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for recurring services: %s", exc
            )

        rows = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE} WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchall()
        return [RecurringService(**dict(row)) for row in rows]

    def replace_for_transaction(
        self, transaction_id: str, services: list[RecurringService]
    ) -> list[RecurringService]:
        """
        Replace all recurring services for a transaction (atomic delete + insert).
        Used when recalculating or updating transaction content.
        """
        created: list[RecurringService] = []

        try:
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
                    rows_to_insert.append(denormalize_keys(data))
                response = (
                    self.supabase.table(self.TABLE)
                    .insert(rows_to_insert)
                    .execute()
                )
                created = [
                    RecurringService(**normalize_keys(row))
                    for row in response.data
                ]

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
            rows_to_insert.append(denormalize_keys(data))

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(rows_to_insert)
                .execute()
            )
            created = [
                RecurringService(**normalize_keys(row)) for row in response.data
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
                    svc.quantity,
                    svc.price_original,
                    str(svc.price_currency),
                    svc.price_pen,
                    svc.cost_unit_1_original,
                    svc.cost_unit_2_original,
                    str(svc.cost_unit_currency),
                    svc.cost_unit_1_pen,
                    svc.cost_unit_2_pen,
                    svc.proveedor,
                ),
            )
        self.sqlite.commit()

