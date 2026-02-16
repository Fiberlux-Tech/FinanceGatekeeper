"""
Application Configuration.

Pydantic Settings model for the FinanceGatekeeper application.
All configuration is loaded from environment variables and .env files.
Inject an AppConfig instance via dependency injection where needed.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import ClassVar, Optional

from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr, model_validator


class AppConfig(BaseSettings):
    """Central configuration loaded from environment variables and defaults."""

    # --- Supabase ---
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: SecretStr = SecretStr("")
    SUPABASE_SERVICE_ROLE_KEY: SecretStr = SecretStr("")  # Reserved for Phase 2+ admin operations

    # --- Email / SMTP ---
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: SecretStr = SecretStr("")
    MAIL_DEFAULT_RECIPIENT: str = ""

    # --- Excel Template Mapping ---
    PLANTILLA_SHEET_NAME: str = "PLANTILLA"

    VARIABLES_TO_EXTRACT: dict[str, str] = Field(default_factory=lambda: {
        "client_name": "C2",
        "salesman": "C3",
        "unidad_negocio": "C4",
        "company_id": "C5",
        "order_id": "C6",
        "mrc": "C7",
        "nrc": "C8",
        "plazo_contrato": "C9",
        "comisiones": "C10",
    })

    # Field type classification for header cell parsing (M6).
    # Single source of truth — excel_parser.py imports these instead of
    # maintaining its own hardcoded sets.  ClassVar so Pydantic-settings
    # does not try to load them from environment variables.
    DECIMAL_FIELDS: ClassVar[frozenset[str]] = frozenset({"mrc", "nrc", "comisiones"})
    INT_FIELDS: ClassVar[frozenset[str]] = frozenset({"company_id", "order_id", "plazo_contrato"})
    BOOL_FIELDS: ClassVar[frozenset[str]] = frozenset({"aplica_carta_fianza"})

    RECURRING_SERVICES_START_ROW: int = 14
    RECURRING_SERVICES_COLUMNS: dict[str, str] = Field(default_factory=lambda: {
        "tipo_servicio": "J",
        "nota": "K",
        "ubicacion": "L",
        "q": "M",
        "p": "N",
        "cu1": "O",
        "cu2": "P",
        "proveedor": "Q",
    })

    FIXED_COSTS_START_ROW: int = 14
    FIXED_COSTS_COLUMNS: dict[str, str] = Field(default_factory=lambda: {
        "categoria": "A",
        "tipo_servicio": "B",
        "ticket": "C",
        "ubicacion": "D",
        "cantidad": "E",
        "costo_unitario": "F",
        "periodo_inicio": "G",
        "duracion_meses": "H",
    })

    # --- SharePoint / OneDrive (Phase 2) ---
    SHAREPOINT_ROOT_OVERRIDE: str = ""
    INBOX_FOLDER_NAME: str = "01_INBOX"
    ARCHIVE_APPROVED_FOLDER_NAME: str = "02_ARCHIVE_APPROVED"
    ARCHIVE_REJECTED_FOLDER_NAME: str = "03_ARCHIVE_REJECTED"

    # --- Excel Parser ---
    MAX_EMPTY_ROWS: int = 5

    # --- Logging ---
    LOG_MAX_BYTES: int = 5_242_880  # 5 MB
    LOG_BACKUP_COUNT: int = 3

    # --- File Watcher ---
    WATCHER_POLL_INTERVAL_S: float = 1.0
    STEADY_STATE_WAIT_S: float = 2.0
    STEADY_STATE_CHECKS: int = 3

    # --- RBAC for Master Variables ---
    MASTER_VARIABLE_ROLES: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "tipo_cambio": {"write_role": "FINANCE", "category": "RATES"},
        "costo_capital": {"write_role": "FINANCE", "category": "RATES"},
        "tasa_carta_fianza": {"write_role": "FINANCE", "category": "RATES"},
    })

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def _warn_missing_env(self) -> "AppConfig":
        """Emit a startup warning when critical configuration is empty.

        Pydantic silently falls back to defaults when ``.env`` is missing.
        This validator logs a warning so operators know the app is running
        with placeholder values — essential for first-run diagnostics.
        """
        _log = logging.getLogger("app.config")

        if not Path(".env").exists():
            _log.warning(
                "No .env file found — all configuration loaded from "
                "environment variables or defaults."
            )

        if not self.SUPABASE_URL:
            _log.warning(
                "SUPABASE_URL is empty — Supabase connectivity is disabled. "
                "The app will operate in offline-only mode."
            )

        if not self.MAIL_USERNAME:
            _log.warning(
                "MAIL_USERNAME is empty — email notifications are disabled."
            )

        return self

    # --- Email Validation ---
    def validate_email_config(self) -> None:
        """Validate that email configuration is complete.

        Raises:
            ValueError: If required email settings are missing.
        """
        if not self.MAIL_USERNAME or not self.MAIL_PASSWORD.get_secret_value():
            raise ValueError("MAIL_USERNAME and MAIL_PASSWORD must be set")
        if not self.MAIL_SERVER:
            raise ValueError("MAIL_SERVER must be set")


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------

_config_instance: Optional[AppConfig] = None
_config_lock: threading.Lock = threading.Lock()


def get_config() -> AppConfig:
    """Return a cached ``AppConfig`` singleton.

    On first call, creates an ``AppConfig`` instance (reading from ``.env``).
    Subsequent calls return the same instance.  Uses a check-lock-check
    pattern to avoid the lock overhead on the fast path while remaining
    thread-safe during first initialisation.

    Prefer direct constructor injection of ``AppConfig`` in new code;
    this factory exists for backward-compatibility with modules that
    import ``get_config()``.
    """
    global _config_instance
    if _config_instance is None:
        with _config_lock:
            if _config_instance is None:
                _config_instance = AppConfig()
    return _config_instance
