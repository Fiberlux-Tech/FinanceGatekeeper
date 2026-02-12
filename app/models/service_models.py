"""
Service Layer Data Transfer Objects.

Pydantic models for validated input/output at service boundaries.
Replaces raw dict passing between layers.
"""

from __future__ import annotations

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
    total_revenue: float = Field(ge=0)
    mrc_pen: float = Field(ge=0)
    plazo_contrato: int = Field(ge=0)
    payback: Optional[int] = None
    gross_margin_ratio: float  # Negative margins are valid (expenses > revenue)
    # GIGALAN-specific fields
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[float] = None


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

    quantity: int = 0
    price_original: float = 0.0
    price_currency: Currency = Currency.PEN
    cost_unit_1_original: float = 0.0
    cost_unit_2_original: float = 0.0
    cost_unit_currency: Currency = Currency.USD

    # Computed output fields -- set by the engine after processing
    price_pen: Optional[float] = None
    ingreso_pen: Optional[float] = None
    cost_unit_1_pen: Optional[float] = None
    cost_unit_2_pen: Optional[float] = None
    egreso_pen: Optional[float] = None


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
    cantidad: int = 0
    costo_unitario_original: float = 0.0
    costo_unitario_currency: Currency = Currency.USD
    periodo_inicio: int = 0
    duracion_meses: int = 1

    # Computed output fields -- set by the engine after processing
    costo_unitario_pen: Optional[float] = None
    total_pen: Optional[float] = None


class FinancialEngineInput(BaseModel):
    """Top-level input to ``calculate_financial_metrics``.

    Encapsulates all parameters needed by the financial engine orchestrator.
    Can be constructed from a dict via ``FinancialEngineInput.model_validate(data_dict)``.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    tipo_cambio: float = 1.0
    plazo_contrato: int = 0
    recurring_services: list[RecurringServiceInput] = Field(default_factory=list)
    mrc_original: float = 0.0
    mrc_currency: Currency = Currency.PEN
    nrc_original: float = 0.0
    nrc_currency: Currency = Currency.PEN
    fixed_costs: list[FixedCostInput] = Field(default_factory=list)
    aplica_carta_fianza: bool = False
    tasa_carta_fianza: float = 0.0
    costo_capital_anual: float = 0.0
    unidad_negocio: str = ""
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[float] = None


# ---------------------------------------------------------------------------
# Financial Engine output models
# ---------------------------------------------------------------------------

class KPIResult(BaseModel):
    """Output model for ``calculate_kpis``.

    Contains all key performance indicators derived from the cash flow
    timeline: NPV, IRR, payback period, revenue/expense totals, and
    gross margin.
    """

    van: float
    tir: Optional[float] = None
    payback: Optional[int] = None
    total_revenue: float
    total_expense: float
    gross_margin: float
    gross_margin_ratio: float


class FinancialMetricsResult(BaseModel):
    """Output model for ``calculate_financial_metrics``.

    Combines KPI results with MRC/NRC details, commission data,
    installation costs, and the full period-by-period timeline.
    The ``timeline`` field is kept as a dict because the nested
    structure (lists of floats, dicts of lists) is deeply nested
    output that does not cross a validation boundary.
    """

    # MRC / NRC
    mrc_original: float
    mrc_pen: float
    nrc_original: float
    nrc_pen: float

    # KPIs
    van: float
    tir: Optional[float] = None
    payback: Optional[int] = None
    total_revenue: float
    total_expense: float
    gross_margin: float
    gross_margin_ratio: float

    # Commission
    comisiones: float
    comisiones_rate: float

    # Installation costs
    costo_instalacion: float
    costo_instalacion_ratio: float

    # Carta Fianza
    costo_carta_fianza: float
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
