"""
FixedCost Model.

Pydantic model replacing the SQLAlchemy FixedCost ORM model.
Fields derived from FixedCost() constructor calls in transactions.py.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from app.models.enums import Currency


class FixedCost(BaseModel):
    """Represents a fixed cost line item in a transaction."""

    id: Optional[int] = None
    transaction_id: Optional[str] = None
    categoria: Optional[str] = None
    tipo_servicio: Optional[str] = None
    ticket: Optional[str] = None
    ubicacion: Optional[str] = None
    cantidad: Optional[float] = Field(default=None, ge=0)
    costo_unitario_original: Optional[float] = Field(default=None, ge=0)
    costo_unitario_currency: Currency = Currency.USD
    costo_unitario_pen: Optional[float] = Field(default=None, ge=0)
    periodo_inicio: int = Field(default=0, ge=0)
    duracion_meses: int = Field(default=1, ge=1)

    model_config = {"from_attributes": True}
