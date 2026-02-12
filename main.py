"""
FinanceGatekeeper Desktop Application Entry Point.

Bootstraps the entire dependency graph via constructor injection,
initialises the local SQLite schema, and launches the CustomTkinter
GUI.  Every subsystem is wired here — no module-level globals.

Usage::

    python main.py
"""

from __future__ import annotations

import atexit
import sys
import traceback
from pathlib import Path

from app.auth import SessionManager
from app.config import get_config
from app.database import DatabaseManager
from app.logger import StructuredLogger, get_logger
from app.schema import initialize_schema
from app.services import create_services
from app.services.session_cache import SessionCacheService
from app.ui.app_shell import AppShell
from app.ui.module_registry import ModuleRegistry
from app.ui.views.dashboard_view import DashboardView
from app.ui.views.settings_view import SettingsView


def main() -> None:
    """Application entry point — wire dependencies and launch the GUI."""
    logger: StructuredLogger = get_logger("main")
    logger.info("Starting FinanceGatekeeper...")

    # ------------------------------------------------------------------
    # 1. Configuration (from .env / environment variables)
    # ------------------------------------------------------------------
    config = get_config()

    # ------------------------------------------------------------------
    # 2. Database Manager (offline-first: Supabase optional, SQLite always)
    # ------------------------------------------------------------------
    db_logger = StructuredLogger(name="database")
    db = DatabaseManager(
        supabase_url=config.SUPABASE_URL,
        supabase_key=config.SUPABASE_ANON_KEY.get_secret_value(),
        sqlite_path=Path("gatekeeper_local.db"),
        logger=db_logger,
    )

    # Belt-and-suspenders: ensure db.close() runs even on unclean exit
    # (e.g. os._exit, signal kill).  DatabaseManager.close() is safe to
    # call multiple times — subsequent calls are no-ops.
    atexit.register(db.close)

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
    services = create_services(
        db=db,
        config=config,
        session=session,
        session_cache=session_cache,
    )

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

    registry.register(
        module_id="settings",
        display_name="Settings",
        icon="\u2699",  # Gear
        factory=lambda parent: SettingsView(
            parent=parent,
            app_settings=services["app_settings_service"],
            path_discovery=services["path_discovery_service"],
            file_watcher=services.get("file_watcher_service"),
            logger=get_logger("settings"),
        ),
        required_roles=frozenset({"SALES", "FINANCE", "ADMIN"}),
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
        registry=registry,
        logger=get_logger("ui"),
    )
    try:
        app.mainloop()
    finally:
        # Guarantees db.close() runs whether mainloop() exits cleanly
        # or raises an exception.  The atexit handler above is a second
        # safety net for harder crashes; this is the primary path.
        db.close()
        logger.info("FinanceGatekeeper shut down.")


def _show_fatal_error(exc: BaseException) -> None:
    """Display a fatal-error dialog so double-click users get feedback.

    Uses ``tkinter.messagebox`` (stdlib) rather than CustomTkinter so
    the dialog works even when CTk initialisation itself is the thing
    that failed.
    """
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        import tkinter
        from tkinter import messagebox

        # A hidden root window is required for messagebox to work
        # when no Tk instance exists yet.
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            title="Finance Gatekeeper — Fatal Error",
            message=(
                "The application encountered an unexpected error and "
                "cannot continue.\n\n"
                f"{type(exc).__name__}: {exc}"
            ),
            detail=detail,
        )
        root.destroy()
    except Exception:
        # If even tkinter fails (headless environment, missing Tcl/Tk),
        # fall back to stderr so the error is not completely swallowed.
        sys.stderr.write(
            f"FATAL: {type(exc).__name__}: {exc}\n{detail}"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _show_fatal_error(exc)
        sys.exit(1)
