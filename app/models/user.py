"""
User Model.

Pydantic model replacing the SQLAlchemy User ORM model.
Fields derived from User() constructor in jit_provisioning.py and
attribute access patterns in users.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.enums import UserRole


class User(BaseModel):
    """Represents a user account.

    ``first_name`` and ``last_name`` are captured at registration and
    concatenated into ``full_name`` for storage.  They are Optional
    because existing database rows only contain ``full_name``.
    """

    id: str  # Supabase UUID
    email: str
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: UserRole
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
