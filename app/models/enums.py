"""
Shared Enumerations for FinanceGatekeeper Models.

All string enumerations for type-safe field constraints.
StrEnum values compare equal to their string equivalents,
so existing code like ``if role == 'SALES'`` continues to work.
"""

from __future__ import annotations
from enum import StrEnum


class UserRole(StrEnum):
    """Valid user roles in the system.

    ``DEACTIVATED`` is a soft-delete sentinel.  Users are never hard-deleted
    to preserve audit trail integrity (CLAUDE.md Section 5).  A deactivated
    user cannot log in but their historical data (transactions, approvals)
    remains intact for compliance reporting.
    """

    SALES = "SALES"
    FINANCE = "FINANCE"
    ADMIN = "ADMIN"
    DEACTIVATED = "DEACTIVATED"


class ApprovalStatus(StrEnum):
    """Transaction approval workflow states.

    ``CANCELLED`` represents a soft-deleted transaction.  Hard deletion is
    intentionally forbidden — transactions have detail rows (``fixed_costs``,
    ``recurring_services``) and immutable audit trails (CLAUDE.md Section 5).
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class Currency(StrEnum):
    """Supported currencies."""

    PEN = "PEN"
    USD = "USD"


class BusinessUnit(StrEnum):
    """Supported business units.

    Business unit is determined from a cell within the Excel file at
    ingestion time, **not** from the folder structure.  All files land
    in a single flat ``01_INBOX`` directory.
    """

    GIGALAN = "GIGALAN"
    ESTADO = "ESTADO"
    CORPORATIVO = "CORPORATIVO"
    MAYORISTA = "MAYORISTA"


class FileStatus(StrEnum):
    """Result of a file readiness check."""

    READY = "READY"
    LOCKED = "LOCKED"
    SYNCING = "SYNCING"


class FileEventType(StrEnum):
    """Watchdog event classification for inbox files."""

    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
