"""
FinanceGatekeeper Desktop Application Entry Point.

Initializes the application, connects to databases, and launches the GUI.
"""

from app.config import get_config
from app.database import db
from app.logger import get_logger

logger = get_logger()


def initialize() -> None:
    """Initialize all application subsystems."""
    config = get_config()

    # Initialize Supabase (cloud database)
    if config.SUPABASE_URL and config.SUPABASE_ANON_KEY:
        db.init_supabase(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)
        logger.info("Supabase client initialized")
    else:
        logger.warning("Supabase credentials not configured — running in offline mode")

    # Initialize SQLite (local sync queue & cache)
    db.init_sqlite()
    logger.info("SQLite local database initialized")


def main() -> None:
    """Application entry point."""
    logger.info("Starting FinanceGatekeeper...")
    initialize()
    # CustomTkinter GUI launch will go here in Phase 1
    logger.info("FinanceGatekeeper initialized. GUI not yet implemented.")


if __name__ == "__main__":
    main()
