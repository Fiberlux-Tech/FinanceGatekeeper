"""
Service Layer Data Transfer Objects.

Pydantic models for validated input/output at service boundaries.
Replaces raw dict passing between layers.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, Field

from app.models.enums import Currency


class CommissionInput(BaseModel):
    """Validated input for commission calculation."""

    unidad_negocio: str
    total_revenue: float = Field(ge=0)
    mrc_pen: float = Field(ge=0)
    plazo_contrato: int = Field(ge=0)
    payback: Optional[int] = None
    gross_margin_ratio: float = Field(ge=0)
    # GIGALAN-specific fields
    gigalan_region: Optional[str] = None
    gigalan_sale_type: Optional[str] = None
    gigalan_old_mrc: Optional[float] = None


class CommissionResult(BaseModel):
    """Output of commission calculation."""

    commission_amount: float = Field(ge=0)
    commission_rate: float = Field(ge=0)


class EmailPayload(BaseModel):
    """Validated email sending request."""

    to_addresses: list[str]
    subject: str
    body_text: str


class PaginatedResult(BaseModel):
    """Generic pagination wrapper."""

    items: list[object]
    total: int
    pages: int
    current_page: int


class ServiceResult(BaseModel):
    """
    Standard service return envelope.

    All service methods return this, providing a consistent contract
    for the view/command layer.
    """

    success: bool
    data: Optional[Union[dict[str, object], list[object], object]] = None
    error: Optional[str] = None
    status_code: int = 200
