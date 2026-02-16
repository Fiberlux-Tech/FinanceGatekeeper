"""
Transaction Model.

Pydantic model replacing the SQLAlchemy Transaction ORM model.
All fields derived from legacy save_transaction() constructor and setattr() calls
in transactions.py — now normalized to snake_case per Section 4D.
"""

from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union
from pydantic import BaseModel, Field, field_validator

from app.models.enums import ApprovalStatus, Currency
from app.models.fixed_cost import FixedCost
from app.models.recurring_service import RecurringService
from app.utils.string_helpers import normalize_keys


class MasterVariablesSnapshot(BaseModel):
    """Frozen snapshot of master variables at transaction creation time."""

    tipo_cambio: Decimal = Field(gt=0)
    costo_capital: Decimal = Field(ge=0)
    tasa_carta_fianza: Decimal = Field(ge=0)
    captured_at: str  # ISO format datetime string

    model_config = {"from_attributes": True}


class FinancialCache(BaseModel):
    """Cached financial calculation results for zero-CPU reads."""

    mrc_original: Optional[Decimal] = None
    mrc_pen: Optional[Decimal] = None
    nrc_original: Optional[Decimal] = None
    nrc_pen: Optional[Decimal] = None
    van: Optional[Decimal] = None
    tir: Optional[Decimal] = None
    payback: Optional[int] = None
    total_revenue: Optional[Decimal] = None
    total_expense: Optional[Decimal] = None
    comisiones: Optional[Decimal] = None
    comisiones_rate: Optional[Decimal] = None
    costo_instalacion: Optional[Decimal] = None
    costo_instalacion_ratio: Optional[Decimal] = None
    gross_margin: Optional[Decimal] = None
    gross_margin_ratio: Optional[Decimal] = None
    costo_carta_fianza: Optional[Decimal] = None
    aplica_carta_fianza: Optional[bool] = None
    timeline: Optional[
        dict[
            str,
            Union[
                list[Decimal],
                list[str],
                list[dict[str, Union[Decimal, int, str, None, list[Decimal]]]],
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
    tipo_cambio: Optional[Decimal] = Field(default=None, gt=0)

    # MRC fields (Monthly Recurring Charge)
    mrc_original: Optional[Decimal] = None
    mrc_currency: Currency = Currency.PEN
    mrc_pen: Optional[Decimal] = None

    # NRC fields (Non-Recurring Charge)
    nrc_original: Optional[Decimal] = None
    nrc_currency: Currency = Currency.PEN
    nrc_pen: Optional[Decimal] = None

    # KPI fields
    van: Optional[Decimal] = None
    tir: Optional[Decimal] = None
    payback: Optional[int] = None
    total_revenue: Optional[Decimal] = None
    total_expense: Optional[Decimal] = None

    # Commission fields
    comisiones: Optional[Decimal] = Field(default=None, ge=0)
    comisiones_rate: Optional[Decimal] = Field(default=None, ge=0)
    costo_instalacion: Optional[Decimal] = Field(default=None, ge=0)
    costo_instalacion_ratio: Optional[Decimal] = None
    gross_margin: Optional[Decimal] = None
    gross_margin_ratio: Optional[Decimal] = None

    # Contract fields
    plazo_contrato: Optional[int] = Field(default=None, ge=0)
    costo_capital_anual: Optional[Decimal] = Field(default=None, ge=0)
    tasa_carta_fianza: Optional[Decimal] = Field(default=None, ge=0)
    costo_carta_fianza: Optional[Decimal] = Field(default=None, ge=0)
    aplica_carta_fianza: bool = False

    # GIGALAN-specific fields
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[Decimal] = None

    # Chain of Custody (CLAUDE.md §5)
    file_sha256: Optional[str] = None

    # User who submitted this deal (Supabase UUID, set at creation)
    created_by: Optional[str] = None

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
    def parse_snapshot(cls: type[Transaction], v: object) -> Optional[MasterVariablesSnapshot]:
        if v is None:
            return None
        if isinstance(v, dict):
            return MasterVariablesSnapshot(**normalize_keys(v))
        return v

    @field_validator("financial_cache", mode="before")
    @classmethod
    def parse_financial_cache(cls: type[Transaction], v: object) -> Optional[FinancialCache]:
        if v is None:
            return None
        if isinstance(v, dict):
            return FinancialCache(**normalize_keys(v))
        return v

    def to_financial_engine_dict(self) -> dict[str, object]:
        """Assemble the data package expected by ``calculate_financial_metrics()``.

        Centralises the 8-line block that was previously copy-pasted in
        ``TransactionCrudService`` and ``TransactionWorkflowService``.
        If the Transaction schema changes, only this method needs updating.

        Returns:
            A dict containing the transaction's scalar fields plus serialised
            ``fixed_costs`` and ``recurring_services`` lists.
        """
        data: dict[str, object] = self.model_dump()
        data["fixed_costs"] = [fc.model_dump() for fc in self.fixed_costs]
        data["recurring_services"] = [rs.model_dump() for rs in self.recurring_services]
        data["gigalan_region"] = self.gigalan_region
        data["gigalan_sale_type"] = self.gigalan_sale_type
        data["gigalan_old_mrc"] = self.gigalan_old_mrc
        data["tasa_carta_fianza"] = self.tasa_carta_fianza
        data["aplica_carta_fianza"] = self.aplica_carta_fianza
        return data

    model_config = {"from_attributes": True}
