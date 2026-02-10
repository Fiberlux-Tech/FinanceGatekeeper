"""
Shared Enumerations for FinanceGatekeeper Models.

All string enumerations for type-safe field constraints.
StrEnum values compare equal to their string equivalents,
so existing code like ``if role == 'SALES'`` continues to work.
"""

from __future__ import annotations
from enum import StrEnum


class UserRole(StrEnum):
    """Valid user roles in the system."""

    SALES = "SALES"
    FINANCE = "FINANCE"
    ADMIN = "ADMIN"


class ApprovalStatus(StrEnum):
    """Transaction approval workflow states."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Currency(StrEnum):
    """Supported currencies."""

    PEN = "PEN"
    USD = "USD"
