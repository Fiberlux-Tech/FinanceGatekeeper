"""
Transaction Repository.

Handles all transaction data access via Supabase (primary) and SQLite (offline cache).
Replaces legacy db.session.query(Transaction), Transaction.query, db.session.add/commit.
Provides aggregation methods for KPI calculations.
"""

from __future__ import annotations

import json
from app.logger import StructuredLogger
import math
from datetime import datetime, timezone
from typing import Optional

from app.database import DatabaseManager
from app.models.enums import ApprovalStatus
from app.models.transaction import Transaction
from app.repositories.base_repository import BaseRepository
from app.utils.string_helpers import normalize_keys, denormalize_keys


class TransactionRepository(BaseRepository):
    """Data access layer for Transaction entities."""

    TABLE = "transactions"

    def __init__(self, db: DatabaseManager, logger: StructuredLogger) -> None:
        super().__init__(db, logger)
        self._ensure_sqlite_table()

    def _ensure_sqlite_table(self) -> None:
        """Create the local SQLite cache table if it doesn't exist."""
        self.sqlite.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                id TEXT PRIMARY KEY,
                unidad_negocio TEXT DEFAULT '',
                client_name TEXT DEFAULT '',
                company_id REAL,
                salesman TEXT DEFAULT '',
                order_id REAL,
                tipo_cambio REAL,
                mrc_original REAL,
                mrc_currency TEXT DEFAULT 'PEN',
                mrc_pen REAL,
                nrc_original REAL,
                nrc_currency TEXT DEFAULT 'PEN',
                nrc_pen REAL,
                van REAL,
                tir REAL,
                payback INTEGER,
                total_revenue REAL,
                total_expense REAL,
                comisiones REAL,
                comisiones_rate REAL,
                costo_instalacion REAL,
                costo_instalacion_ratio REAL,
                gross_margin REAL,
                gross_margin_ratio REAL,
                plazo_contrato INTEGER,
                costo_capital_anual REAL,
                tasa_carta_fianza REAL,
                costo_carta_fianza REAL,
                aplica_carta_fianza INTEGER DEFAULT 0,
                gigalan_region TEXT,
                gigalan_sale_type TEXT,
                gigalan_old_mrc REAL,
                master_variables_snapshot TEXT,
                approval_status TEXT DEFAULT 'PENDING',
                submission_date TIMESTAMP,
                approval_date TIMESTAMP,
                rejection_note TEXT,
                financial_cache TEXT
            )
            """
        )
        self.sqlite.commit()

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

        row = self.sqlite.execute(
            f"SELECT * FROM {self.TABLE} WHERE id = ?", (transaction_id,)
        ).fetchone()
        if row:
            return self._parse_sqlite_transaction(dict(row))
        return None

    def get_paginated(
        self,
        page: int = 1,
        per_page: int = 30,
        salesman_filter: Optional[str] = None,
        search: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, object]:
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
                or_filter = (
                    f"client_name.ilike.%{search}%,"
                    f"salesman.ilike.%{search}%,"
                    f"id.ilike.%{search}%,"
                    f"unidad_negocio.ilike.%{search}%"
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
        return self._get_paginated_sqlite(
            page, per_page, salesman_filter, search, start_date, end_date
        )

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
            # SQLite fallback
            sets = ", ".join(f"{k} = ?" for k in update_data)
            vals = list(update_data.values()) + [transaction_id]
            self.sqlite.execute(
                f"UPDATE {self.TABLE} SET {sets} WHERE id = ?", vals
            )
            self.sqlite.commit()
            self._queue_pending_sync("update_status", transaction_id, update_data)

        return self.get_by_id(transaction_id)

    def get_pending_aggregates(
        self,
        salesman_filter: Optional[str] = None,
    ) -> dict[str, object]:
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
                normalized = normalize_keys(row)
                mrc = normalized.get("mrc_pen")
                com = normalized.get("comisiones")
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
                from datetime import timedelta
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=months_back * 30)
                ).isoformat()
                query = query.gte("submission_date", cutoff)

            response = query.execute()

            margins = [
                float(normalize_keys(row).get("gross_margin_ratio", 0))
                for row in response.data
                if normalize_keys(row).get("gross_margin_ratio") is not None
            ]

            return sum(margins) / len(margins) if margins else 0.0

        except Exception as exc:
            self._logger.warning(
                "Supabase unavailable for average margin: %s", exc
            )

        # SQLite fallback
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
            from datetime import timedelta
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=months_back * 30)
            ).isoformat()
            sql += " AND submission_date >= ?"
            params.append(cutoff)

        row = self.sqlite.execute(sql, params).fetchone()
        if row:
            avg = dict(row).get("avg_margin")
            return float(avg) if avg is not None else 0.0
        return 0.0

    # --- Private helpers ---

    def _parse_transaction(self, data: dict[str, object]) -> Transaction:
        """Parse a Supabase row into a Transaction model."""
        normalized = normalize_keys(data)
        # Parse JSON fields stored as strings
        for json_field in ("master_variables_snapshot", "financial_cache"):
            val = normalized.get(json_field)
            if isinstance(val, str):
                try:
                    normalized[json_field] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    normalized[json_field] = None
        return Transaction(**normalized)

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
        return denormalize_keys(data)

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

        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{k} = excluded.{k}" for k in data if k != "id")

        self.sqlite.execute(
            f"""
            INSERT INTO {self.TABLE} ({columns})
            VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
            """,
            list(data.values()),
        )
        self.sqlite.commit()

    def _get_paginated_sqlite(
        self,
        page: int,
        per_page: int,
        salesman_filter: Optional[str],
        search: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> dict[str, object]:
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
            where_clauses.append(
                "(client_name LIKE ? OR salesman LIKE ? OR id LIKE ? OR unidad_negocio LIKE ?)"
            )
            pattern = f"%{search}%"
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

