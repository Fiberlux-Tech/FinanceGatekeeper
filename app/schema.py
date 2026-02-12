"""
Centralized SQLite Schema Initialization.

Defines the canonical schema for the FinanceGatekeeper local database and
provides a single entry-point -- :func:`initialize_schema` -- that creates
all required tables idempotently.  A lightweight ``schema_version`` table
tracks applied migrations so that future schema changes can be rolled
forward without data loss.

Migration Strategy
~~~~~~~~~~~~~~~~~~
- **Fresh databases** (version 0): all tables are created in one shot from
  :data:`_TABLE_DEFINITIONS`.
- **Existing databases** (version N > 0): only incremental migrations
  registered in :data:`_MIGRATIONS` are executed.  ``CREATE TABLE IF NOT
  EXISTS`` is *not* re-run — it cannot add columns to existing tables.
- The entire upgrade (migrations + version bump) is wrapped in a single
  SQLite transaction.  On failure the database rolls back to version N
  and the next startup retries.

Adding a New Migration
~~~~~~~~~~~~~~~~~~~~~~
1. Bump :data:`CURRENT_SCHEMA_VERSION`.
2. Update the relevant DDL in :data:`_TABLE_DEFINITIONS` (for fresh installs).
3. Write a ``_migrate_vN_to_vN+1()`` function (use ``ALTER TABLE`` with a
   :func:`_column_exists` guard for idempotency).
4. Register the function in :data:`_MIGRATIONS`.

Per CLAUDE.md:
    - Offline-First Thinking: SQLite is the primary local store.
    - Audit Trail: the ``audit_log`` table supplies queryable persistence.
    - Chain of Custody: ``sync_queue`` buffers outbound changes.

Usage::

    import sqlite3
    from app.logger import StructuredLogger
    from app.schema import initialize_schema

    conn = sqlite3.connect("gatekeeper.db")
    logger = StructuredLogger(name="schema")
    initialize_schema(conn, logger)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from app.logger import StructuredLogger

__all__ = ["CURRENT_SCHEMA_VERSION", "initialize_schema"]

# ---------------------------------------------------------------------------
# Schema version -- bump this whenever a migration is added.
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION: int = 8

# ---------------------------------------------------------------------------
# DDL statements for every table in the local database.
# ---------------------------------------------------------------------------
_TABLE_DEFINITIONS: list[str] = [
    # -- single-row version tracker -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        version INTEGER NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- offline-first outbound sync buffer -----------------------------------
    """
    CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        operation TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        attempted_at TIMESTAMP,
        error_message TEXT
    )
    """,
    # -- persistent structured audit trail ------------------------------------
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        details TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- profiles (local cache — mirrors Supabase profiles table) -------------
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'SALES'
             CHECK (role IN ('SALES', 'FINANCE', 'ADMIN')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- transactions (local cache) -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        unidad_negocio TEXT DEFAULT '',
        client_name TEXT DEFAULT '',
        company_id INTEGER,
        salesman TEXT DEFAULT '',
        order_id INTEGER,
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
        financial_cache TEXT,
        file_sha256 TEXT
    )
    """,
    # -- fixed_costs (local cache) --------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS fixed_costs (
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
        duracion_meses INTEGER DEFAULT 1,
        FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
    )
    """,
    # -- recurring_services (local cache) -------------------------------------
    """
    CREATE TABLE IF NOT EXISTS recurring_services (
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
        proveedor TEXT,
        FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
    )
    """,
    # -- master_variables (local cache, append-only) --------------------------
    """
    CREATE TABLE IF NOT EXISTS master_variables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        variable_name TEXT NOT NULL,
        variable_value REAL NOT NULL,
        category TEXT NOT NULL,
        user_id TEXT NOT NULL,
        comment TEXT,
        date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- app_settings (key-value local preferences) ---------------------------
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- encrypted_sessions (offline auth cache) ------------------------------
    """
    CREATE TABLE IF NOT EXISTS encrypted_sessions (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        encrypted_payload BLOB NOT NULL,
        nonce BLOB NOT NULL,
        tag BLOB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # -- indexes for Phase 4 query performance --------------------------------
    "CREATE INDEX IF NOT EXISTS idx_transactions_approval_status ON transactions(approval_status)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_salesman ON transactions(salesman)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_submission_date ON transactions(submission_date)",
    "CREATE INDEX IF NOT EXISTS idx_fixed_costs_transaction_id ON fixed_costs(transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_recurring_services_transaction_id ON recurring_services(transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue(status)",
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _ensure_version_table(conn: sqlite3.Connection) -> None:
    """Create the ``schema_version`` table if it does not yet exist.

    This is executed *before* any version check so that a brand-new
    database can be bootstrapped cleanly.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or ``0`` if unset.

    A return value of ``0`` indicates the database has never been
    initialised, and all tables must be created from scratch.
    """
    cursor: sqlite3.Cursor = conn.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    )
    row: tuple[int] | None = cursor.fetchone()
    return row[0] if row is not None else 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Upsert the single-row version tracker to *version*.

    Does **not** commit — the caller is responsible for transaction
    management so that version updates are atomic with schema changes.
    """
    conn.execute(
        """
        INSERT INTO schema_version (id, version) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET version = excluded.version,
                                      applied_at = CURRENT_TIMESTAMP
        """,
        (version,),
    )


def _create_all_tables(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Execute every DDL statement in :data:`_TABLE_DEFINITIONS`.

    Only used for **fresh** databases (version 0) where no tables exist
    yet.  Each statement uses ``CREATE TABLE IF NOT EXISTS`` for safety.

    Does **not** commit — the caller is responsible for transaction
    management.
    """
    for ddl in _TABLE_DEFINITIONS:
        conn.execute(ddl)
    logger.info(
        f"All {len(_TABLE_DEFINITIONS)} tables created or verified successfully."
    )


_ALLOWED_TABLES: frozenset[str] = frozenset({
    "schema_version",
    "sync_queue",
    "audit_log",
    "profiles",
    "transactions",
    "fixed_costs",
    "recurring_services",
    "master_variables",
    "app_settings",
    "encrypted_sessions",
})
"""Tables that may be referenced in dynamic PRAGMA queries.

This allowlist prevents SQL injection in :func:`_column_exists`.  Every
table defined in :data:`_TABLE_DEFINITIONS` must be listed here.  See
TODO.md H-1.
"""


def _column_exists(
    conn: sqlite3.Connection, table: str, column: str,
) -> bool:
    """Check whether *column* already exists in *table*.

    Raises:
        ValueError: If *table* is not in :data:`_ALLOWED_TABLES`.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"Invalid table name: {table!r}. "
            f"Allowed tables: {sorted(_ALLOWED_TABLES)}"
        )
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _migrate_v4_to_v5(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Add ``file_sha256`` column to ``transactions`` (C-5 fix).

    Required by CLAUDE.md §5 — Chain of Custody.  The column is
    nullable so existing rows remain valid.
    """
    if not _column_exists(conn, "transactions", "file_sha256"):
        conn.execute("ALTER TABLE transactions ADD COLUMN file_sha256 TEXT")
        logger.info("Migration v4→v5: added file_sha256 column to transactions.")


def _migrate_v5_to_v6(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Recreate ``fixed_costs`` and ``recurring_services`` with FK constraints.

    SQLite does not support ``ALTER TABLE … ADD CONSTRAINT``, so the only
    way to retrofit a FOREIGN KEY is the four-step rename dance:

        1. Create ``{table}_new`` with the FK clause.
        2. Copy all existing rows.
        3. Drop the old table.
        4. Rename ``{table}_new`` → ``{table}``.

    Does **not** commit — the caller is responsible for transaction
    management.
    """
    # -- fixed_costs -----------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fixed_costs_new (
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
            duracion_meses INTEGER DEFAULT 1,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO fixed_costs_new
            (id, transaction_id, categoria, tipo_servicio, ticket,
             ubicacion, cantidad, costo_unitario_original,
             costo_unitario_currency, costo_unitario_pen,
             periodo_inicio, duracion_meses)
        SELECT
            id, transaction_id, categoria, tipo_servicio, ticket,
            ubicacion, cantidad, costo_unitario_original,
            costo_unitario_currency, costo_unitario_pen,
            periodo_inicio, duracion_meses
        FROM fixed_costs
        """
    )
    conn.execute("DROP TABLE fixed_costs")
    conn.execute("ALTER TABLE fixed_costs_new RENAME TO fixed_costs")

    # -- recurring_services ----------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recurring_services_new (
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
            proveedor TEXT,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO recurring_services_new
            (id, transaction_id, tipo_servicio, nota, ubicacion,
             quantity, price_original, price_currency, price_pen,
             cost_unit_1_original, cost_unit_2_original,
             cost_unit_currency, cost_unit_1_pen, cost_unit_2_pen,
             proveedor)
        SELECT
            id, transaction_id, tipo_servicio, nota, ubicacion,
            quantity, price_original, price_currency, price_pen,
            cost_unit_1_original, cost_unit_2_original,
            cost_unit_currency, cost_unit_1_pen, cost_unit_2_pen,
            proveedor
        FROM recurring_services
        """
    )
    conn.execute("DROP TABLE recurring_services")
    conn.execute("ALTER TABLE recurring_services_new RENAME TO recurring_services")

    logger.info(
        "Migration v5→v6: recreated fixed_costs and recurring_services "
        "with FOREIGN KEY constraints."
    )


def _migrate_v6_to_v7(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Change ``company_id`` and ``order_id`` from REAL to INTEGER.

    These columns store whole-number identifiers (SAP company codes, order
    numbers) that were originally typed as REAL by mistake.  The Pydantic
    ``Transaction`` model already declares them as ``Optional[int]``, so
    the SQLite column type must match to avoid silent float storage.

    SQLite does not support ``ALTER TABLE … ALTER COLUMN``, so we use the
    four-step rename dance (create new → copy → drop old → rename).

    Does **not** commit — the caller is responsible for transaction
    management.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions_new (
            id TEXT PRIMARY KEY,
            unidad_negocio TEXT DEFAULT '',
            client_name TEXT DEFAULT '',
            company_id INTEGER,
            salesman TEXT DEFAULT '',
            order_id INTEGER,
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
            financial_cache TEXT,
            file_sha256 TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO transactions_new
            (id, unidad_negocio, client_name,
             company_id, salesman, order_id,
             tipo_cambio,
             mrc_original, mrc_currency, mrc_pen,
             nrc_original, nrc_currency, nrc_pen,
             van, tir, payback,
             total_revenue, total_expense,
             comisiones, comisiones_rate,
             costo_instalacion, costo_instalacion_ratio,
             gross_margin, gross_margin_ratio,
             plazo_contrato,
             costo_capital_anual, tasa_carta_fianza, costo_carta_fianza,
             aplica_carta_fianza,
             gigalan_region, gigalan_sale_type, gigalan_old_mrc,
             master_variables_snapshot,
             approval_status, submission_date, approval_date,
             rejection_note, financial_cache, file_sha256)
        SELECT
            id, unidad_negocio, client_name,
            CAST(company_id AS INTEGER), salesman, CAST(order_id AS INTEGER),
            tipo_cambio,
            mrc_original, mrc_currency, mrc_pen,
            nrc_original, nrc_currency, nrc_pen,
            van, tir, payback,
            total_revenue, total_expense,
            comisiones, comisiones_rate,
            costo_instalacion, costo_instalacion_ratio,
            gross_margin, gross_margin_ratio,
            plazo_contrato,
            costo_capital_anual, tasa_carta_fianza, costo_carta_fianza,
            aplica_carta_fianza,
            gigalan_region, gigalan_sale_type, gigalan_old_mrc,
            master_variables_snapshot,
            approval_status, submission_date, approval_date,
            rejection_note, financial_cache, file_sha256
        FROM transactions
        """
    )
    conn.execute("DROP TABLE transactions")
    conn.execute("ALTER TABLE transactions_new RENAME TO transactions")

    logger.info(
        "Migration v6→v7: changed company_id and order_id from REAL to INTEGER "
        "in transactions table."
    )


def _migrate_v7_to_v8(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Add database indexes for Phase 4 query performance.

    Creates indexes on frequently queried columns across the three core
    transaction tables and the offline sync buffer.  These indexes
    accelerate dashboard filtering (approval_status, salesman,
    submission_date), detail-row lookups by foreign key (transaction_id),
    and sync-queue processing (status).

    All statements use ``CREATE INDEX IF NOT EXISTS`` for idempotency.

    Does **not** commit -- the caller is responsible for transaction
    management.
    """
    index_statements: list[str] = [
        "CREATE INDEX IF NOT EXISTS idx_transactions_approval_status ON transactions(approval_status)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_salesman ON transactions(salesman)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_submission_date ON transactions(submission_date)",
        "CREATE INDEX IF NOT EXISTS idx_fixed_costs_transaction_id ON fixed_costs(transaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_recurring_services_transaction_id ON recurring_services(transaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue(status)",
    ]
    for stmt in index_statements:
        conn.execute(stmt)

    logger.info(
        "Migration v7→v8: created 6 indexes for Phase 4 query performance."
    )


# ---------------------------------------------------------------------------
# Migration registry — maps *target* version to its migration function.
# ---------------------------------------------------------------------------

MigrationFunc = Callable[[sqlite3.Connection, StructuredLogger], None]

_MIGRATIONS: dict[int, MigrationFunc] = {
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
    7: _migrate_v6_to_v7,
    8: _migrate_v7_to_v8,
}


def _run_incremental_migrations(
    conn: sqlite3.Connection,
    logger: StructuredLogger,
    from_version: int,
    to_version: int,
) -> None:
    """Run all registered migrations between *from_version* and *to_version*.

    Migrations are executed in ascending version order.  Only versions
    in the half-open range ``(from_version, to_version]`` are applied.
    Each migration function must be idempotent.

    Does **not** commit — the caller is responsible for transaction
    management.
    """
    versions_to_apply: list[int] = sorted(
        v for v in _MIGRATIONS if from_version < v <= to_version
    )

    if not versions_to_apply:
        logger.info("No incremental migrations to apply.")
        return

    logger.info(
        f"Applying {len(versions_to_apply)} migration(s): "
        f"{' → '.join(str(v) for v in versions_to_apply)}"
    )
    for version in versions_to_apply:
        logger.info(f"Running migration to version {version} …")
        _MIGRATIONS[version](conn, logger)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initialize_schema(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Ensure the local SQLite database matches the current schema version.

    Workflow:
        1. Guarantee the ``schema_version`` table exists (separate commit).
        2. Read the stored version number (``0`` for a fresh database).
        3. If the stored version equals or exceeds
           :data:`CURRENT_SCHEMA_VERSION`, return immediately.
        4. Otherwise, upgrade within a **single atomic transaction**:

           - **Fresh database** (version 0): create all tables from
             :data:`_TABLE_DEFINITIONS`.
           - **Existing database** (version N > 0): run incremental
             migrations from :data:`_MIGRATIONS` for versions in
             ``(N, CURRENT_SCHEMA_VERSION]``.
           - Update the version tracker.
           - Commit.  On failure the entire upgrade is rolled back so the
             version number stays at N and the next startup retries.

    This function is designed to be called on every application startup
    and is fully idempotent.

    Args:
        conn: An open SQLite connection.
        logger: A :class:`~app.logger.StructuredLogger` instance for
            structured log output.
    """
    _ensure_version_table(conn)
    current: int = _get_schema_version(conn)

    if current >= CURRENT_SCHEMA_VERSION:
        logger.info(f"Schema is up to date (version {current}).")
        return

    logger.info(
        f"Upgrading schema from version {current} "
        f"to {CURRENT_SCHEMA_VERSION} …"
    )

    try:
        if current == 0:
            _create_all_tables(conn, logger)
        else:
            _run_incremental_migrations(
                conn, logger, current, CURRENT_SCHEMA_VERSION,
            )

        _set_schema_version(conn, CURRENT_SCHEMA_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.error(
            f"Schema migration failed — rolled back to version {current}."
        )
        raise

    logger.info(f"Schema initialised at version {CURRENT_SCHEMA_VERSION}.")
