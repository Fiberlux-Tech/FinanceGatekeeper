"""
MasterVariable Model.

Pydantic model replacing the SQLAlchemy MasterVariable ORM model.
Fields derived from MasterVariable() constructor in variables.py.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class MasterVariable(BaseModel):
    """Represents a versioned system variable (exchange rates, cost of capital, etc.)."""

    id: Optional[int] = None
    variable_name: str
    variable_value: float
    category: str
    user_id: str
    comment: Optional[str] = None
    date_recorded: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"from_attributes": True}
