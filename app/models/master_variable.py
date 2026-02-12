"""
MasterVariable Model.

Pydantic model replacing the SQLAlchemy MasterVariable ORM model.
Fields derived from MasterVariable() constructor in variables.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MasterVariable(BaseModel):
    """Represents a versioned system variable (exchange rates, cost of capital, etc.).

    ``variable_name`` and ``category`` are stripped and validated
    to prevent phantom variables caused by whitespace or empty strings.
    ``variable_value`` must be non-negative (exchange rates, cost of
    capital, and fianza rates are always >= 0).
    """

    id: Optional[int] = None
    variable_name: str = Field(min_length=1)
    variable_value: float = Field(ge=0)
    category: str = Field(min_length=1)
    user_id: str
    comment: Optional[str] = None
    date_recorded: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("variable_name", "category", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        """Strip leading/trailing whitespace to prevent phantom entries."""
        if isinstance(v, str):
            return v.strip()
        return v

    model_config = {"from_attributes": True}
