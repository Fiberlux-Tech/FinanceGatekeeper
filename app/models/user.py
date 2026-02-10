"""
User Model.

Pydantic model replacing the SQLAlchemy User ORM model.
Fields derived from User() constructor in jit_provisioning.py and
attribute access patterns in users.py.
"""

from __future__ import annotations
from pydantic import BaseModel

from app.models.enums import UserRole


class User(BaseModel):
    """Represents a user account."""

    id: str  # Supabase UUID
    email: str
    full_name: str
    role: UserRole

    model_config = {"from_attributes": True}
