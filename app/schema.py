"""
Centralized SQLite Schema Initialization.

Defines the canonical schema for the FinanceGatekeeper local database and
provides a single entry-point -- :func:`initialize_schema` -- that creates
all required tables idempotently.  A lightweight ``schema_version`` table
tracks applied migrations so that future schema changes can be rolled
forward without data loss.

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

from app.logger import StructuredLogger

__all__ = ["CURRENT_SCHEMA_VERSION", "initialize_schema"]

# ---------------------------------------------------------------------------
# Schema version -- bump this whenever a migration is added.
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION: int = 3

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
    # -- users (local cache) --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        full_name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'SALES'
    )
    """,
    # -- transactions (local cache) -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS transactions (
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
        duracion_meses INTEGER DEFAULT 1
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
        proveedor TEXT
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
    """Upsert the single-row version tracker to *version*."""
    conn.execute(
        """
        INSERT INTO schema_version (id, version) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET version = excluded.version,
                                      applied_at = CURRENT_TIMESTAMP
        """,
        (version,),
    )
    conn.commit()


def _create_all_tables(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Execute every DDL statement in :data:`_TABLE_DEFINITIONS`.

    Each statement uses ``CREATE TABLE IF NOT EXISTS`` so re-running is
    safe even if the database already contains some tables.
    """
    for ddl in _TABLE_DEFINITIONS:
        conn.execute(ddl)
    conn.commit()
    logger.info(
        f"All {len(_TABLE_DEFINITIONS)} tables created or verified successfully."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initialize_schema(conn: sqlite3.Connection, logger: StructuredLogger) -> None:
    """Ensure the local SQLite database matches the current schema version.

    Workflow:
        1. Guarantee the ``schema_version`` table exists.
        2. Read the stored version number (``0`` for a fresh database).
        3. If the stored version is below :data:`CURRENT_SCHEMA_VERSION`,
           create all tables and update the version tracker.
        4. If the version is already current, log "up to date" and return.

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
        f"Upgrading schema from version {current} to {CURRENT_SCHEMA_VERSION} ..."
    )
    _create_all_tables(conn, logger)
    _set_schema_version(conn, CURRENT_SCHEMA_VERSION)
    logger.info(f"Schema initialised at version {CURRENT_SCHEMA_VERSION}.")
