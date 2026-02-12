"""
Transaction Repository.

Handles all transaction data access via Supabase (primary) and SQLite (offline cache).
Replaces legacy db.session.query(Transaction), Transaction.query, db.session.add/commit.
Provides aggregation methods for KPI calculations.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict

from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.enums import ApprovalStatus
from app.models.transaction import Transaction
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import sanitize_postgrest_value


class PaginatedTransactions(TypedDict):
    """Typed return value for paginated transaction queries."""

    items: list[Transaction]
    total: int
    pages: int
    current_page: int


class PendingAggregates(TypedDict):
    """Typed return value for pending transaction aggregates."""

    total_pending_mrc: float
    pending_count: int
    total_pending_comisiones: float


class TransactionRepository(BaseRepository):
    """Data access layer for Transaction entities.

    **No ``delete()`` method — by design.**

    Transactions are never hard-deleted.  Each transaction has child rows
    in ``fixed_costs`` and ``recurring_services`` linked by foreign key,
    plus an immutable audit trail (CLAUDE.md Section 5).  Hard deletion
    would violate referential integrity and destroy compliance evidence.

    Instead, use :meth:`soft_delete` to transition a transaction's
    ``approval_status`` to ``CANCELLED``.  Cancelled transactions remain
    queryable for audit and reporting but are excluded from active KPIs.
    """

    TABLE = "transactions"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)

    def get_by_id(self, transaction_id: str) -> Optional[Transaction]:
        """Fetch a transaction by ID. Tries Supabase first, falls back to SQLite."""
        try:
            response = (
                self.supabase.table(self.TABLE)
                .select("*")
                .eq("id", transaction_id)
                .maybe_single()
                .execute()
            )
            if response.data:
                return self._parse_transaction(response.data)
        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for transaction lookup: %s", exc
            )

        try:
            row = self.sqlite.execute(
                f"SELECT * FROM {self.TABLE} WHERE id = ?", (transaction_id,)
            ).fetchone()
            if row:
                return self._parse_sqlite_transaction(dict(row))
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for get_by_id (transactions): %s",
                sqlite_exc,
            )
        return None

    def get_paginated(
        self,
        page: int = 1,
        per_page: int = 30,
        salesman_filter: Optional[str] = None,
        search: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> PaginatedTransactions:
        """
        Fetch paginated transactions with optional filters.

        Returns dict with keys: items (list[Transaction]), total (int),
        pages (int), current_page (int).
        """
        try:
            count_q = self.supabase.table(self.TABLE).select("id", count="exact")
            data_q = self.supabase.table(self.TABLE).select("*")

            if salesman_filter:
                count_q = count_q.eq("salesman", salesman_filter)
                data_q = data_q.eq("salesman", salesman_filter)
            if start_date:
                count_q = count_q.gte("submission_date", start_date.isoformat())
                data_q = data_q.gte("submission_date", start_date.isoformat())
            if end_date:
                count_q = count_q.lte("submission_date", end_date.isoformat())
                data_q = data_q.lte("submission_date", end_date.isoformat())
            if search:
                safe_search = sanitize_postgrest_value(search)
                or_filter = (
                    f"client_name.ilike.%{safe_search}%,"
                    f"salesman.ilike.%{safe_search}%,"
                    f"id.ilike.%{safe_search}%,"
                    f"unidad_negocio.ilike.%{safe_search}%"
                )
                count_q = count_q.or_(or_filter)
                data_q = data_q.or_(or_filter)

            # Execute count
            count_response = count_q.execute()
            total = count_response.count if count_response.count is not None else 0

            # Execute data with pagination
            offset = (page - 1) * per_page
            data_q = data_q.order("submission_date", desc=True).range(
                offset, offset + per_page - 1
            )
            data_response = data_q.execute()

            items = [
                self._parse_transaction(row) for row in data_response.data
            ]
            pages = math.ceil(total / per_page) if per_page > 0 else 1

            return {
                "items": items,
                "total": total,
                "pages": pages,
                "current_page": page,
            }

        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for paginated transactions: %s", exc
            )

        # SQLite fallback
        try:
            return self._get_paginated_sqlite(
                page, per_page, salesman_filter, search, start_date, end_date
            )
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for paginated transactions: %s",
                sqlite_exc,
            )
            return {
                "items": [],
                "total": 0,
                "pages": 1,
                "current_page": page,
            }

    def create(self, transaction: Transaction) -> Transaction:
        """Insert a new transaction. Writes to Supabase and caches to SQLite."""
        data = self._serialize_for_supabase(transaction)

        try:
            response = (
                self.supabase.table(self.TABLE)
                .insert(data)
                .execute()
            )
            created = self._parse_transaction(response.data[0])
            self._cache_to_sqlite(created)
            self._logger.info("Transaction created: %s", created.id)
            return created
        except Exception as exc:
            self._logger.error(
                "Failed to create transaction in Supabase: %s", exc
            )
            self._cache_to_sqlite(transaction)
            self._queue_pending_sync("insert", transaction.id, data)
            return transaction

    def update(self, transaction: Transaction) -> Transaction:
        """Update an existing transaction. Writes to Supabase and updates SQLite cache."""
        data = self._serialize_for_supabase(transaction)

        try:
            response = (
                self.supabase.table(self.TABLE)
                .update(data)
                .eq("id", transaction.id)
                .execute()
            )
            if response.data:
                updated = self._parse_transaction(response.data[0])
                self._cache_to_sqlite(updated)
                return updated
        except Exception as exc:
            self._logger.error(
                "Failed to update transaction in Supabase: %s", exc
            )
            self._cache_to_sqlite(transaction)
            self._queue_pending_sync("update", transaction.id, data)

        return transaction

    def update_status(
        self,
        transaction_id: str,
        status: ApprovalStatus,
        approval_date: Optional[datetime] = None,
        rejection_note: Optional[str] = None,
    ) -> Optional[Transaction]:
        """Update a transaction's approval status."""
        update_data: dict[str, object] = {"approval_status": str(status)}
        if approval_date:
            update_data["approval_date"] = approval_date.isoformat()
        if rejection_note is not None:
            update_data["rejection_note"] = rejection_note

        try:
            response = (
                self.supabase.table(self.TABLE)
                .update(update_data)
                .eq("id", transaction_id)
                .execute()
            )
            if response.data:
                updated = self._parse_transaction(response.data[0])
                self._cache_to_sqlite(updated)
                return updated
        except Exception as exc:
            self._logger.error(
                "Failed to update transaction status in Supabase: %s", exc
            )
            # SQLite fallback — hardcoded allowlist (H-2 fix)
            _STATUS_UPDATE_COLUMNS = frozenset({
                "approval_status", "approval_date", "rejection_note",
            })
            safe_data = {
                k: v for k, v in update_data.items()
                if k in _STATUS_UPDATE_COLUMNS
            }
            if not safe_data:
                self._logger.error(
                    "update_status called with no valid columns for "
                    "transaction %s — nothing to update.",
                    transaction_id,
                )
                return self.get_by_id(transaction_id)
            sets = ", ".join(f"{k} = ?" for k in safe_data)
            vals = list(safe_data.values()) + [transaction_id]
            self.sqlite.execute(
                f"UPDATE {self.TABLE} SET {sets} WHERE id = ?", vals
            )
            self._commit()
            self._queue_pending_sync("update_status", transaction_id, update_data)

        return self.get_by_id(transaction_id)

    def soft_delete(self, transaction_id: str) -> bool:
        """Cancel a transaction (soft-delete via status transition).

        Transitions the transaction's ``approval_status`` to ``CANCELLED``.
        Hard deletion is intentionally forbidden — transactions have child
        detail rows (``fixed_costs``, ``recurring_services``) and immutable
        audit trails (CLAUDE.md Section 5).

        Only transactions in ``PENDING`` or ``REJECTED`` status can be
        cancelled.  Approved transactions represent committed financial
        records and must not be cancelled without a separate reversal
        workflow.

        Args:
            transaction_id: The UUID of the transaction to cancel.

        Returns:
            ``True`` if the transaction was successfully cancelled (or was
            already cancelled), ``False`` if the transaction does not exist
            or is in a non-cancellable state (``APPROVED``).
        """
        existing = self.get_by_id(transaction_id)
        if existing is None:
            self._logger.warning(
                "Cannot soft-delete transaction %s: not found.",
                transaction_id,
            )
            return False

        if existing.approval_status == ApprovalStatus.CANCELLED:
            self._logger.info(
                "Transaction %s is already cancelled — no-op.",
                transaction_id,
            )
            return True

        if existing.approval_status == ApprovalStatus.APPROVED:
            self._logger.warning(
                "Cannot soft-delete transaction %s: APPROVED transactions "
                "require a formal reversal workflow, not cancellation.",
                transaction_id,
            )
            return False

        result = self.update_status(
            transaction_id, ApprovalStatus.CANCELLED,
        )
        if result is not None and result.approval_status == ApprovalStatus.CANCELLED:
            self._logger.info(
                "Transaction soft-deleted (CANCELLED): %s", transaction_id,
            )
            return True

        self._logger.error(
            "Soft-delete may have failed for transaction %s — status "
            "update did not confirm CANCELLED state.",
            transaction_id,
        )
        return False

    def get_pending_aggregates(
        self,
        salesman_filter: Optional[str] = None,
    ) -> PendingAggregates:
        """
        Get aggregated KPI data for pending transactions.

        Returns dict with: total_pending_mrc, pending_count, total_pending_comisiones.
        """
        try:
            query = (
                self.supabase.table(self.TABLE)
                .select("mrc_pen, comisiones")
                .eq("approval_status", str(ApprovalStatus.PENDING))
            )
            if salesman_filter:
                query = query.eq("salesman", salesman_filter)

            response = query.execute()

            total_mrc = 0.0
            total_comisiones = 0.0
            count = len(response.data)

            for row in response.data:
                mrc = row.get("mrc_pen")
                com = row.get("comisiones")
                if mrc is not None:
                    total_mrc += float(mrc)
                if com is not None:
                    total_comisiones += float(com)

            return {
                "total_pending_mrc": total_mrc,
                "pending_count": count,
                "total_pending_comisiones": total_comisiones,
            }

        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for pending aggregates: %s", exc
            )

        # SQLite fallback
        try:
            sql = f"""
                SELECT
                    COALESCE(SUM(mrc_pen), 0) AS total_pending_mrc,
                    COUNT(*) AS pending_count,
                    COALESCE(SUM(comisiones), 0) AS total_pending_comisiones
                FROM {self.TABLE}
                WHERE approval_status = ?
            """
            params: list[str] = [str(ApprovalStatus.PENDING)]
            if salesman_filter:
                sql += " AND salesman = ?"
                params.append(salesman_filter)

            row = self.sqlite.execute(sql, params).fetchone()
            if row:
                row_dict = dict(row)
                return {
                    "total_pending_mrc": float(row_dict.get("total_pending_mrc", 0)),
                    "pending_count": int(row_dict.get("pending_count", 0)),
                    "total_pending_comisiones": float(
                        row_dict.get("total_pending_comisiones", 0)
                    ),
                }
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for get_pending_aggregates (transactions): %s",
                sqlite_exc,
            )
        return {
            "total_pending_mrc": 0.0,
            "pending_count": 0,
            "total_pending_comisiones": 0.0,
        }

    def get_average_margin(
        self,
        salesman_filter: Optional[str] = None,
        months_back: Optional[int] = None,
        status: Optional[str] = None,
    ) -> float:
        """Get the average gross margin ratio, optionally filtered."""
        try:
            query = (
                self.supabase.table(self.TABLE)
                .select("gross_margin_ratio")
            )
            if salesman_filter:
                query = query.eq("salesman", salesman_filter)
            if status:
                query = query.eq("approval_status", status)
            if months_back:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=months_back * 30)
                ).isoformat()
                query = query.gte("submission_date", cutoff)

            response = query.execute()

            margins = [
                float(row.get("gross_margin_ratio", 0))
                for row in response.data
                if row.get("gross_margin_ratio") is not None
            ]

            return sum(margins) / len(margins) if margins else 0.0

        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for average margin: %s", exc
            )

        # SQLite fallback
        try:
            sql = f"""
                SELECT AVG(gross_margin_ratio) AS avg_margin
                FROM {self.TABLE}
                WHERE gross_margin_ratio IS NOT NULL
            """
            params: list[str] = []
            if salesman_filter:
                sql += " AND salesman = ?"
                params.append(salesman_filter)
            if status:
                sql += " AND approval_status = ?"
                params.append(status)
            if months_back:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=months_back * 30)
                ).isoformat()
                sql += " AND submission_date >= ?"
                params.append(cutoff)

            row = self.sqlite.execute(sql, params).fetchone()
            if row:
                avg = dict(row).get("avg_margin")
                return float(avg) if avg is not None else 0.0
        except sqlite3.Error as sqlite_exc:
            self._logger.error(
                "SQLite fallback also failed for get_average_margin (transactions): %s",
                sqlite_exc,
            )
        return 0.0

    # --- Private helpers ---

    def _parse_transaction(self, data: dict[str, object]) -> Transaction:
        """Parse a Supabase row into a Transaction model."""
        # Parse JSON fields stored as strings
        for json_field in ("master_variables_snapshot", "financial_cache"):
            val = data.get(json_field)
            if isinstance(val, str):
                try:
                    data[json_field] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    data[json_field] = None
        return Transaction(**data)

    def _parse_sqlite_transaction(self, row: dict[str, object]) -> Transaction:
        """Parse a SQLite row into a Transaction model."""
        # Convert boolean field
        if "aplica_carta_fianza" in row:
            row["aplica_carta_fianza"] = bool(row["aplica_carta_fianza"])
        # Parse JSON fields
        for json_field in ("master_variables_snapshot", "financial_cache"):
            val = row.get(json_field)
            if isinstance(val, str):
                try:
                    row[json_field] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    row[json_field] = None
        return Transaction(**row)

    def _serialize_for_supabase(
        self, transaction: Transaction
    ) -> dict[str, object]:
        """Convert a Transaction model to a dict suitable for Supabase insert/update."""
        data = transaction.model_dump(exclude={"fixed_costs", "recurring_services"})
        # Serialize nested models to JSON strings
        if data.get("master_variables_snapshot") is not None:
            data["master_variables_snapshot"] = json.dumps(
                data["master_variables_snapshot"], default=str
            )
        if data.get("financial_cache") is not None:
            data["financial_cache"] = json.dumps(
                data["financial_cache"], default=str
            )
        # Convert datetime objects
        for dt_field in ("submission_date", "approval_date"):
            val = data.get(dt_field)
            if isinstance(val, datetime):
                data[dt_field] = val.isoformat()
        # Convert enums
        for enum_field in ("approval_status", "mrc_currency", "nrc_currency"):
            val = data.get(enum_field)
            if val is not None:
                data[enum_field] = str(val)
        return data

    # Hardcoded column allowlist for SQLite cache (H-2 fix).
    # Must match the Transaction model fields (excluding relationships).
    _SQLITE_COLUMNS: tuple[str, ...] = (
        "id",
        "unidad_negocio",
        "client_name",
        "company_id",
        "salesman",
        "order_id",
        "tipo_cambio",
        "mrc_original",
        "mrc_currency",
        "mrc_pen",
        "nrc_original",
        "nrc_currency",
        "nrc_pen",
        "van",
        "tir",
        "payback",
        "total_revenue",
        "total_expense",
        "comisiones",
        "comisiones_rate",
        "costo_instalacion",
        "costo_instalacion_ratio",
        "gross_margin",
        "gross_margin_ratio",
        "plazo_contrato",
        "costo_capital_anual",
        "tasa_carta_fianza",
        "costo_carta_fianza",
        "aplica_carta_fianza",
        "gigalan_region",
        "gigalan_sale_type",
        "gigalan_old_mrc",
        "file_sha256",
        "master_variables_snapshot",
        "approval_status",
        "submission_date",
        "approval_date",
        "rejection_note",
        "financial_cache",
    )

    def _cache_to_sqlite(self, transaction: Transaction) -> None:
        """Write transaction to local SQLite cache."""
        data = transaction.model_dump(exclude={"fixed_costs", "recurring_services"})
        # Serialize JSON fields
        for json_field in ("master_variables_snapshot", "financial_cache"):
            if data.get(json_field) is not None:
                data[json_field] = json.dumps(data[json_field], default=str)
        # Convert datetime
        for dt_field in ("submission_date", "approval_date"):
            val = data.get(dt_field)
            if isinstance(val, datetime):
                data[dt_field] = val.isoformat()
        # Convert enums and booleans
        data["aplica_carta_fianza"] = int(data.get("aplica_carta_fianza", False))
        for enum_field in ("approval_status", "mrc_currency", "nrc_currency"):
            val = data.get(enum_field)
            if val is not None:
                data[enum_field] = str(val)

        # Use hardcoded allowlist — only known columns enter the SQL statement
        columns_sql = ", ".join(self._SQLITE_COLUMNS)
        placeholders = ", ".join("?" for _ in self._SQLITE_COLUMNS)
        updates = ", ".join(
            f"{col} = excluded.{col}"
            for col in self._SQLITE_COLUMNS if col != "id"
        )
        values = [data.get(col) for col in self._SQLITE_COLUMNS]

        self.sqlite.execute(
            f"""
            INSERT INTO {self.TABLE} ({columns_sql})
            VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
            """,
            values,
        )
        self._commit()

    def _get_paginated_sqlite(
        self,
        page: int,
        per_page: int,
        salesman_filter: Optional[str],
        search: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> PaginatedTransactions:
        """SQLite fallback for paginated queries."""
        where_clauses: list[str] = []
        params: list[object] = []

        if salesman_filter:
            where_clauses.append("salesman = ?")
            params.append(salesman_filter)
        if start_date:
            where_clauses.append("submission_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            where_clauses.append("submission_date <= ?")
            params.append(end_date.isoformat())
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where_clauses.append(
                "(client_name LIKE ? ESCAPE '\\' "
                "OR salesman LIKE ? ESCAPE '\\' "
                "OR id LIKE ? ESCAPE '\\' "
                "OR unidad_negocio LIKE ? ESCAPE '\\')"
            )
            pattern = f"%{escaped}%"
            params.extend([pattern, pattern, pattern, pattern])

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Count
        count_row = self.sqlite.execute(
            f"SELECT COUNT(*) AS cnt FROM {self.TABLE}{where_sql}", params
        ).fetchone()
        total = dict(count_row).get("cnt", 0) if count_row else 0

        # Data
        offset = (page - 1) * per_page
        data_params = list(params) + [per_page, offset]
        rows = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE}{where_sql} ORDER BY submission_date DESC LIMIT ? OFFSET ?",
            data_params,
        ).fetchall()

        items = [self._parse_sqlite_transaction(dict(row)) for row in rows]
        pages = math.ceil(int(total) / per_page) if per_page > 0 else 1

        return {
            "items": items,
            "total": int(total),
            "pages": pages,
            "current_page": page,
        }

