"""
Transaction Model.

Pydantic model replacing the SQLAlchemy Transaction ORM model.
All fields derived from legacy save_transaction() constructor and setattr() calls
in transactions.py — now normalized to snake_case per Section 4D.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, Union
from pydantic import BaseModel, Field, field_validator

from app.models.enums import ApprovalStatus, Currency
from app.models.fixed_cost import FixedCost
from app.models.recurring_service import RecurringService
from app.utils.string_helpers import normalize_keys


class MasterVariablesSnapshot(BaseModel):
    """Frozen snapshot of master variables at transaction creation time."""

    tipo_cambio: float = Field(gt=0)
    costo_capital: float = Field(ge=0)
    tasa_carta_fianza: float = Field(ge=0)
    captured_at: str  # ISO format datetime string

    model_config = {"from_attributes": True}


class FinancialCache(BaseModel):
    """Cached financial calculation results for zero-CPU reads."""

    mrc_original: Optional[float] = None
    mrc_pen: Optional[float] = None
    nrc_original: Optional[float] = None
    nrc_pen: Optional[float] = None
    van: Optional[float] = None
    tir: Optional[float] = None
    payback: Optional[int] = None
    total_revenue: Optional[float] = None
    total_expense: Optional[float] = None
    comisiones: Optional[float] = None
    comisiones_rate: Optional[float] = None
    costo_instalacion: Optional[float] = None
    costo_instalacion_ratio: Optional[float] = None
    gross_margin: Optional[float] = None
    gross_margin_ratio: Optional[float] = None
    costo_carta_fianza: Optional[float] = None
    aplica_carta_fianza: Optional[bool] = None
    timeline: Optional[
        dict[
            str,
            Union[
                list[float],
                list[str],
                list[dict[str, Union[float, int, str, None, list[float]]]],
            ],
        ]
    ] = None

    model_config = {"from_attributes": True}


class Transaction(BaseModel):
    """Represents a financial transaction record."""

    id: str
    unidad_negocio: str = ""
    client_name: str = ""
    company_id: Optional[int] = None
    salesman: str = ""
    order_id: Optional[int] = None
    tipo_cambio: Optional[float] = Field(default=None, gt=0)

    # MRC fields (Monthly Recurring Charge)
    mrc_original: Optional[float] = None
    mrc_currency: Currency = Currency.PEN
    mrc_pen: Optional[float] = None

    # NRC fields (Non-Recurring Charge)
    nrc_original: Optional[float] = None
    nrc_currency: Currency = Currency.PEN
    nrc_pen: Optional[float] = None

    # KPI fields
    van: Optional[float] = None
    tir: Optional[float] = None
    payback: Optional[int] = None
    total_revenue: Optional[float] = None
    total_expense: Optional[float] = None

    # Commission fields
    comisiones: Optional[float] = Field(default=None, ge=0)
    comisiones_rate: Optional[float] = Field(default=None, ge=0)
    costo_instalacion: Optional[float] = Field(default=None, ge=0)
    costo_instalacion_ratio: Optional[float] = None
    gross_margin: Optional[float] = None
    gross_margin_ratio: Optional[float] = None

    # Contract fields
    plazo_contrato: Optional[int] = Field(default=None, ge=0)
    costo_capital_anual: Optional[float] = Field(default=None, ge=0)
    tasa_carta_fianza: Optional[float] = Field(default=None, ge=0)
    costo_carta_fianza: Optional[float] = Field(default=None, ge=0)
    aplica_carta_fianza: bool = False

    # GIGALAN-specific fields
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[float] = None

    # Chain of Custody (CLAUDE.md §5)
    file_sha256: Optional[str] = None

    # Metadata
    master_variables_snapshot: Optional[MasterVariablesSnapshot] = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    submission_date: Optional[datetime] = None
    approval_date: Optional[datetime] = None
    rejection_note: Optional[str] = None
    financial_cache: Optional[FinancialCache] = None

    # Relationships (populated by repository layer)
    fixed_costs: list[FixedCost] = Field(default_factory=list)
    recurring_services: list[RecurringService] = Field(default_factory=list)

    @field_validator("master_variables_snapshot", mode="before")
    @classmethod
    def parse_snapshot(cls, v: object) -> Optional[MasterVariablesSnapshot]:
        if v is None:
            return None
        if isinstance(v, dict):
            return MasterVariablesSnapshot(**normalize_keys(v))
        return v

    @field_validator("financial_cache", mode="before")
    @classmethod
    def parse_financial_cache(cls, v: object) -> Optional[FinancialCache]:
        if v is None:
            return None
        if isinstance(v, dict):
            return FinancialCache(**normalize_keys(v))
        return v

    model_config = {"from_attributes": True}
