"""
Repository Layer Package.

Provides data-access abstractions over Supabase (cloud) and SQLite (local cache).
All database operations flow through repositories — services never access
db.supabase or db.sqlite directly.

Usage:
    from app.repositories.transaction_repository import TransactionRepository
    from app.repositories.user_repository import UserRepository
"""

from app.repositories.base_repository import BaseRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.master_variable_repository import MasterVariableRepository
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository

__all__ = [
    "BaseRepository",
    "TransactionRepository",
    "UserRepository",
    "MasterVariableRepository",
    "FixedCostRepository",
    "RecurringServiceRepository",
]
