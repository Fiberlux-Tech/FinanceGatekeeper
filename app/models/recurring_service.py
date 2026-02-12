"""
RecurringService Model.

Pydantic model replacing the SQLAlchemy RecurringService ORM model.
Fields derived from RecurringService() constructor calls in transactions.py.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from app.models.enums import Currency


class RecurringService(BaseModel):
    """Represents a recurring service line item in a transaction."""

    id: Optional[int] = None
    transaction_id: str
    tipo_servicio: Optional[str] = None
    nota: Optional[str] = None
    ubicacion: Optional[str] = None
    quantity: Optional[float] = Field(
        default=None, ge=0,
        description="Quantity of service units",
    )

    # Price fields (sale price to client)
    price_original: Optional[float] = Field(
        default=None, ge=0,
        description="Unit sale price in original currency",
    )
    price_currency: Currency = Field(
        default=Currency.PEN,
        description="Currency of the sale price",
    )
    price_pen: Optional[float] = Field(
        default=None, ge=0,
        description="Unit sale price converted to PEN",
    )

    # Cost Unit fields (costs from provider)
    cost_unit_1_original: Optional[float] = Field(
        default=None, ge=0,
        description="Primary provider cost per unit in original currency",
    )
    cost_unit_2_original: Optional[float] = Field(
        default=None, ge=0,
        description="Secondary provider cost per unit in original currency",
    )
    cost_unit_currency: Currency = Field(
        default=Currency.USD,
        description="Currency of provider costs",
    )
    cost_unit_1_pen: Optional[float] = Field(
        default=None, ge=0,
        description="Primary provider cost converted to PEN",
    )
    cost_unit_2_pen: Optional[float] = Field(
        default=None, ge=0,
        description="Secondary provider cost converted to PEN",
    )

    proveedor: Optional[str] = None

    model_config = {"from_attributes": True}
