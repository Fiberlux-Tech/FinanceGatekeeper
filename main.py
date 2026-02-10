"""
FinanceGatekeeper Desktop Application Entry Point.

Bootstraps the entire dependency graph via constructor injection,
initialises the local SQLite schema, and launches the CustomTkinter
GUI.  Every subsystem is wired here — no module-level globals.

Usage::

    python main.py
"""

from __future__ import annotations

from pathlib import Path

from app.auth import SessionManager
from app.config import AppConfig
from app.database import DatabaseManager
from app.logger import StructuredLogger, get_logger
from app.schema import initialize_schema
from app.services import create_services
from app.services.session_cache import SessionCacheService
from app.ui.app_shell import AppShell
from app.ui.module_registry import ModuleRegistry
from app.ui.views.dashboard_view import DashboardView


def main() -> None:
    """Application entry point — wire dependencies and launch the GUI."""
    logger: StructuredLogger = get_logger("main")
    logger.info("Starting FinanceGatekeeper...")

    # ------------------------------------------------------------------
    # 1. Configuration (from .env / environment variables)
    # ------------------------------------------------------------------
    config = AppConfig()

    # ------------------------------------------------------------------
    # 2. Database Manager (offline-first: Supabase optional, SQLite always)
    # ------------------------------------------------------------------
    db_logger = StructuredLogger(name="database")
    db = DatabaseManager(
        supabase_url=config.SUPABASE_URL,
        supabase_key=config.SUPABASE_ANON_KEY,
        sqlite_path=Path("gatekeeper_local.db"),
        logger=db_logger,
    )

    # ------------------------------------------------------------------
    # 3. SQLite Schema Initialization (all 10 tables, idempotent)
    # ------------------------------------------------------------------
    schema_logger = StructuredLogger(name="schema")
    initialize_schema(db.sqlite, schema_logger)

    # ------------------------------------------------------------------
    # 4. Session Manager
    # ------------------------------------------------------------------
    session = SessionManager()

    # ------------------------------------------------------------------
    # 5. Encrypted Session Cache (offline auth)
    # ------------------------------------------------------------------
    session_cache = SessionCacheService(
        db=db,
        logger=StructuredLogger(name="session_cache"),
    )

    # ------------------------------------------------------------------
    # 6. Service Container (repositories + services, single composition root)
    # ------------------------------------------------------------------
    services = create_services(db=db, config=config)

    # ------------------------------------------------------------------
    # 7. Module Registry (plug-and-play modules)
    # ------------------------------------------------------------------
    registry = ModuleRegistry(logger=get_logger("modules"))

    registry.register(
        module_id="gatekeeper",
        display_name="Gatekeeper",
        icon="\U0001F6E1",  # Shield
        factory=lambda parent: DashboardView(
            parent=parent,
            session=session,
            db=db,
            logger=get_logger("dashboard"),
        ),
        required_roles=frozenset({"SALES", "FINANCE", "ADMIN"}),
        default=True,
    )

    # ------------------------------------------------------------------
    # 8. Launch the GUI (blocks until window closes)
    # ------------------------------------------------------------------
    logger.info("Launching GUI...")
    app = AppShell(
        config=config,
        db=db,
        session=session,
        services=services,
        session_cache=session_cache,
        registry=registry,
        logger=get_logger("ui"),
    )
    app.mainloop()

    # ------------------------------------------------------------------
    # 9. Cleanup
    # ------------------------------------------------------------------
    db.close()
    logger.info("FinanceGatekeeper shut down.")


if __name__ == "__main__":
    main()
