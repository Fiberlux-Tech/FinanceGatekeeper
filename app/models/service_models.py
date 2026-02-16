"""
Service Layer Data Transfer Objects.

Pydantic models for validated input/output at service boundaries.
Replaces raw dict passing between layers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Currency

T = TypeVar("T")

__all__ = [
    "CommissionInput",
    "FinancialEngineInput",
    "FinancialMetricsResult",
    "FixedCostInput",
    "KPIResult",
    "RecurringServiceInput",
    "ServiceResult",
]


# ---------------------------------------------------------------------------
# Commission models
# ---------------------------------------------------------------------------

class CommissionInput(BaseModel):
    """Validated input for commission calculation."""

    unidad_negocio: str
    total_revenue: Decimal = Field(ge=0)
    mrc_pen: Decimal = Field(ge=0)
    plazo_contrato: int = Field(ge=1, le=480)
    payback: Optional[int] = None
    gross_margin_ratio: Decimal = Field(ge=-1.0, le=2.0)
    # GIGALAN-specific fields
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Financial Engine input models
# ---------------------------------------------------------------------------

class RecurringServiceInput(BaseModel):
    """Validated input for a single recurring service line item.

    Fields are populated from the Excel parser or frontend payload.
    Computed PEN fields are set by the financial engine after currency
    conversion and are Optional until that point.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    quantity: int = Field(default=0, ge=0)
    price_original: Decimal = Decimal("0")
    price_currency: Currency = Currency.PEN
    cost_unit_1_original: Decimal = Decimal("0")
    cost_unit_2_original: Decimal = Decimal("0")
    cost_unit_currency: Currency = Currency.USD

    # Computed output fields -- set by the engine after processing
    price_pen: Optional[Decimal] = None
    ingreso_pen: Optional[Decimal] = None
    cost_unit_1_pen: Optional[Decimal] = None
    cost_unit_2_pen: Optional[Decimal] = None
    egreso_pen: Optional[Decimal] = None


class FixedCostInput(BaseModel):
    """Validated input for a single fixed cost line item.

    Fields are populated from the Excel parser or frontend payload.
    Computed PEN fields are set by the financial engine after currency
    conversion and are Optional until that point.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: Optional[str] = None
    categoria: Optional[str] = None
    tipo_servicio: Optional[str] = None
    cantidad: int = Field(default=0, ge=0)
    costo_unitario_original: Decimal = Decimal("0")
    costo_unitario_currency: Currency = Currency.USD
    periodo_inicio: int = 0
    duracion_meses: int = 1

    # Computed output fields -- set by the engine after processing
    costo_unitario_pen: Optional[Decimal] = None
    total_pen: Optional[Decimal] = None


class FinancialEngineInput(BaseModel):
    """Top-level input to ``calculate_financial_metrics``.

    Encapsulates all parameters needed by the financial engine orchestrator.
    Can be constructed from a dict via ``FinancialEngineInput.model_validate(data_dict)``.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    tipo_cambio: Decimal = Decimal("1")
    plazo_contrato: int = 0
    recurring_services: list[RecurringServiceInput] = Field(default_factory=list)
    mrc_original: Decimal = Decimal("0")
    mrc_currency: Currency = Currency.PEN
    nrc_original: Decimal = Decimal("0")
    nrc_currency: Currency = Currency.PEN
    fixed_costs: list[FixedCostInput] = Field(default_factory=list)
    aplica_carta_fianza: bool = False
    tasa_carta_fianza: Decimal = Decimal("0")
    costo_capital_anual: Decimal = Decimal("0")
    unidad_negocio: str = ""
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Financial Engine output models
# ---------------------------------------------------------------------------

class KPIResult(BaseModel):
    """Output model for ``calculate_kpis``.

    Contains all key performance indicators derived from the cash flow
    timeline: NPV, IRR, payback period, revenue/expense totals, and
    gross margin.
    """

    van: Decimal
    tir: Optional[Decimal] = None
    payback: Optional[int] = None
    total_revenue: Decimal
    total_expense: Decimal
    gross_margin: Decimal
    gross_margin_ratio: Decimal


class FinancialMetricsResult(BaseModel):
    """Output model for ``calculate_financial_metrics``.

    Combines KPI results with MRC/NRC details, commission data,
    installation costs, and the full period-by-period timeline.
    The ``timeline`` field is kept as a dict because the nested
    structure (lists of floats, dicts of lists) is deeply nested
    output that does not cross a validation boundary.
    """

    # MRC / NRC
    mrc_original: Decimal
    mrc_pen: Decimal
    nrc_original: Decimal
    nrc_pen: Decimal

    # KPIs
    van: Decimal
    tir: Optional[Decimal] = None
    payback: Optional[int] = None
    total_revenue: Decimal
    total_expense: Decimal
    gross_margin: Decimal
    gross_margin_ratio: Decimal

    # Commission
    comisiones: Decimal
    comisiones_rate: Decimal

    # Installation costs
    costo_instalacion: Decimal
    costo_instalacion_ratio: Decimal

    # Carta Fianza
    costo_carta_fianza: Decimal
    aplica_carta_fianza: bool

    # Timeline (deeply nested dict -- not validated beyond top-level)
    timeline: dict[str, object]


# ---------------------------------------------------------------------------
# Generic service models
# ---------------------------------------------------------------------------

class ServiceResult(BaseModel, Generic[T]):
    """
    Standard service return envelope.

    All service methods return this, providing a consistent contract
    for the view/command layer.

    Generic over ``T`` so callers can annotate return types precisely
    (e.g. ``ServiceResult[dict[str, float]]``).  Using bare
    ``ServiceResult(...)`` without a type parameter is still valid --
    Pydantic v2 treats the unparameterised form as ``ServiceResult[Any]``
    at runtime, preserving full backward compatibility.
    """

    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    status_code: int = 200
