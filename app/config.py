"""
Application Configuration.

Pydantic Settings model for the FinanceGatekeeper application.
All configuration is loaded from environment variables and .env files.
Inject an AppConfig instance via dependency injection where needed.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class AppConfig(BaseSettings):
    """Central configuration loaded from environment variables and defaults."""

    # --- Supabase ---
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # --- Email / SMTP ---
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
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

    # --- RBAC for Master Variables ---
    MASTER_VARIABLE_ROLES: dict[str, dict[str, str]] = Field(default_factory=lambda: {
        "tipo_cambio": {"write_role": "FINANCE", "category": "RATES"},
        "costo_capital": {"write_role": "FINANCE", "category": "RATES"},
        "tasa_carta_fianza": {"write_role": "FINANCE", "category": "RATES"},
    })

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # --- Email Validation ---
    def validate_email_config(self) -> None:
        """Validate that email configuration is complete.

        Raises:
            ValueError: If required email settings are missing.
        """
        if not self.MAIL_USERNAME or not self.MAIL_PASSWORD:
            raise ValueError("MAIL_USERNAME and MAIL_PASSWORD must be set")
        if not self.MAIL_SERVER:
            raise ValueError("MAIL_SERVER must be set")
