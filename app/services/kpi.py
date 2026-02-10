"""
KPI Service.

Provides dashboard metrics: pending MRC totals, transaction counts,
commission sums, and average gross margin ratios. All queries are
RBAC-scoped -- SALES users see only their own transactions, while
FINANCE and ADMIN see all.

Refactored from legacy Flask service:
    - Stripped: g.current_user, db.session, func.*, SQLAlchemy ORM
    - Eliminated: _apply_kpi_filters() -- filtering now lives in the repository
    - Injected: TransactionRepository, logging.Logger
    - All public methods receive CurrentUser explicitly (no implicit globals)
"""

from __future__ import annotations

import logging
from typing import Optional

from app.auth import CurrentUser
from app.models.enums import UserRole
from app.models.service_models import ServiceResult
from app.repositories.transaction_repository import TransactionRepository
from app.services.base_service import BaseService


class KPIService(BaseService):
    """
    Service layer for Key Performance Indicator calculations.

    Delegates all aggregation queries to TransactionRepository, applying
    RBAC-based salesman filtering before each call.
    """

    def __init__(
        self,
        repo: TransactionRepository,
        logger: logging.Logger,
    ) -> None:
        super().__init__(logger)
        self._repo = repo

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_salesman_filter(
        self,
        current_user: CurrentUser,
    ) -> Optional[str]:
        """
        Determine whether the current user's queries should be scoped
        to their own transactions.

        SALES users see only their own data. FINANCE and ADMIN see all.

        Args:
            current_user: The authenticated user making the request.

        Returns:
            The salesman full_name to filter by, or None for unrestricted access.
        """
        if current_user.role == UserRole.SALES:
            return current_user.full_name
        return None

    # ------------------------------------------------------------------
    # Consolidated summary
    # ------------------------------------------------------------------

    def get_kpi_summary(
        self,
        current_user: CurrentUser,
        months_back: Optional[int] = None,
        status_filter: Optional[str] = None,
    ) -> ServiceResult:
        """
        Consolidated KPI fetch -- single service call for all dashboard metrics.

        Executes two repository queries:
            1. Pending aggregates (MRC sum, count, comisiones sum) -- always
               filtered to PENDING status.
            2. Average gross margin -- optionally filtered by status and
               time window.

        Args:
            current_user: The authenticated user (determines RBAC scope).
            months_back: Optional lookback window for margin calculation
                         (approximate months, using 30-day periods).
            status_filter: Optional approval status filter for the margin
                           query (e.g. "APPROVED", "PENDING").

        Returns:
            ServiceResult with data dict containing:
                - total_pending_mrc (float)
                - pending_count (int)
                - total_pending_comisiones (float)
                - average_gross_margin_ratio (float)
        """
        salesman_filter: Optional[str] = self._resolve_salesman_filter(
            current_user,
        )

        try:
            # Query 1: Pending aggregates (MRC, count, comisiones)
            pending_aggs: dict[str, object] = (
                self._repo.get_pending_aggregates(
                    salesman_filter=salesman_filter,
                )
            )

            # Query 2: Average gross margin (separate filters)
            avg_margin: float = self._repo.get_average_margin(
                salesman_filter=salesman_filter,
                months_back=months_back,
                status=status_filter,
            )

            return ServiceResult(
                success=True,
                data={
                    "total_pending_mrc": float(
                        pending_aggs.get("total_pending_mrc", 0.0),
                    ),
                    "pending_count": int(
                        pending_aggs.get("pending_count", 0),
                    ),
                    "total_pending_comisiones": float(
                        pending_aggs.get("total_pending_comisiones", 0.0),
                    ),
                    "average_gross_margin_ratio": avg_margin,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to compute KPI summary: %s", exc, exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error computing KPI summary: {exc}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Individual KPI methods (backward compatibility)
    # ------------------------------------------------------------------

    def get_pending_mrc_sum(
        self,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Sum of MRC (Monthly Recurring Charge) for pending transactions.

        RBAC-scoped: SALES users see only their own pending MRC.

        Args:
            current_user: The authenticated user.

        Returns:
            ServiceResult with total_pending_mrc float value.
        """
        salesman_filter: Optional[str] = self._resolve_salesman_filter(
            current_user,
        )

        try:
            pending_aggs: dict[str, object] = (
                self._repo.get_pending_aggregates(
                    salesman_filter=salesman_filter,
                )
            )
            return ServiceResult(
                success=True,
                data={
                    "total_pending_mrc": float(
                        pending_aggs.get("total_pending_mrc", 0.0),
                    ),
                    "user_role": current_user.role,
                    "full_name": current_user.full_name,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to compute pending MRC sum: %s", exc, exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {exc}",
                status_code=500,
            )

    def get_pending_transaction_count(
        self,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Count of pending transactions.

        RBAC-scoped: SALES users see only their own pending count.

        Args:
            current_user: The authenticated user.

        Returns:
            ServiceResult with pending_count integer value.
        """
        salesman_filter: Optional[str] = self._resolve_salesman_filter(
            current_user,
        )

        try:
            pending_aggs: dict[str, object] = (
                self._repo.get_pending_aggregates(
                    salesman_filter=salesman_filter,
                )
            )
            return ServiceResult(
                success=True,
                data={
                    "pending_count": int(
                        pending_aggs.get("pending_count", 0),
                    ),
                    "user_role": current_user.role,
                    "full_name": current_user.full_name,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to compute pending transaction count: %s",
                exc,
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {exc}",
                status_code=500,
            )

    def get_pending_comisiones_sum(
        self,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Sum of comisiones (commissions) for pending transactions.

        RBAC-scoped: SALES users see only their own pending commissions.

        Args:
            current_user: The authenticated user.

        Returns:
            ServiceResult with total_pending_comisiones float value.
        """
        salesman_filter: Optional[str] = self._resolve_salesman_filter(
            current_user,
        )

        try:
            pending_aggs: dict[str, object] = (
                self._repo.get_pending_aggregates(
                    salesman_filter=salesman_filter,
                )
            )
            return ServiceResult(
                success=True,
                data={
                    "total_pending_comisiones": float(
                        pending_aggs.get("total_pending_comisiones", 0.0),
                    ),
                    "user_role": current_user.role,
                    "full_name": current_user.full_name,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to compute pending comisiones sum: %s",
                exc,
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {exc}",
                status_code=500,
            )

    def get_average_gross_margin(
        self,
        current_user: CurrentUser,
        months_back: Optional[int] = None,
        status_filter: Optional[str] = None,
    ) -> ServiceResult:
        """
        Average gross margin ratio, with optional time and status filters.

        RBAC-scoped: SALES users see only their own margin average.

        Args:
            current_user: The authenticated user.
            months_back: Optional lookback window (approximate months).
            status_filter: Optional approval status filter.

        Returns:
            ServiceResult with average_gross_margin_ratio float value and
            applied filter metadata.
        """
        salesman_filter: Optional[str] = self._resolve_salesman_filter(
            current_user,
        )

        try:
            avg_margin: float = self._repo.get_average_margin(
                salesman_filter=salesman_filter,
                months_back=months_back,
                status=status_filter,
            )
            return ServiceResult(
                success=True,
                data={
                    "average_gross_margin_ratio": avg_margin,
                    "user_role": current_user.role,
                    "full_name": current_user.full_name,
                    "filters": {
                        "months_back": months_back,
                        "status_filter": status_filter,
                    },
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to compute average gross margin: %s",
                exc,
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {exc}",
                status_code=500,
            )
