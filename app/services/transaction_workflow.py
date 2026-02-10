"""
Transaction Workflow Service.

Handles approval, rejection, and recalculation workflows for transactions.
Refactored from legacy transactions.py -- class-based, repository-backed,
with structured audit logging and ServiceResult envelope.

Functions ported:
    - approve_transaction
    - reject_transaction
    - recalculate_commission_and_metrics
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Optional

from app.auth import CurrentUser
from app.logger import StructuredLogger
from app.models.enums import ApprovalStatus, UserRole
from app.models.service_models import ServiceResult
from app.models.transaction import Transaction
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.base_service import BaseService
from app.services.email_service import EmailService
from app.services.financial_engine import calculate_financial_metrics
from app.services.transaction_crud import TransactionCrudService
from app.utils.audit import log_audit_event
from app.utils.general import convert_to_json_safe


class TransactionWorkflowService(BaseService):
    """
    Service handling transaction state transitions: approve, reject,
    and financial recalculation.

    Dependencies are injected via __init__ -- no global state, no Flask.
    """

    def __init__(
        self,
        transaction_repo: TransactionRepository,
        fixed_cost_repo: FixedCostRepository,
        recurring_service_repo: RecurringServiceRepository,
        email_service: EmailService,
        crud_service: TransactionCrudService,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._tx_repo = transaction_repo
        self._fc_repo = fixed_cost_repo
        self._rs_repo = recurring_service_repo
        self._email_service = email_service
        self._crud_service = crud_service

    # ------------------------------------------------------------------
    # Private helper: recalculate and apply financial metrics
    # ------------------------------------------------------------------

    def _recalculate_and_apply_metrics(
        self,
        transaction: Transaction,
    ) -> dict[str, object]:
        """
        Assemble data package, recalculate financial metrics, and apply
        them to the transaction model.

        Args:
            transaction: The Transaction object to recalculate.

        Returns:
            The clean metrics dictionary that was applied.
        """
        tx_data: dict[str, object] = transaction.model_dump()
        tx_data["fixed_costs"] = [fc.model_dump() for fc in transaction.fixed_costs]
        tx_data["recurring_services"] = [rs.model_dump() for rs in transaction.recurring_services]
        tx_data["gigalan_region"] = transaction.gigalan_region
        tx_data["gigalan_sale_type"] = transaction.gigalan_sale_type
        tx_data["gigalan_old_mrc"] = transaction.gigalan_old_mrc
        tx_data["tasa_carta_fianza"] = transaction.tasa_carta_fianza
        tx_data["aplica_carta_fianza"] = transaction.aplica_carta_fianza

        # Recalculate financial metrics
        financial_metrics: dict[str, object] = calculate_financial_metrics(tx_data)
        clean_metrics: dict[str, object] = convert_to_json_safe(financial_metrics)

        # Update transaction with fresh calculations
        for key, value in clean_metrics.items():
            if hasattr(transaction, key):
                setattr(transaction, key, value)

        transaction.costo_instalacion = clean_metrics.get("costo_instalacion")
        transaction.mrc_original = clean_metrics.get("mrc_original")
        transaction.mrc_pen = clean_metrics.get("mrc_pen")
        transaction.nrc_original = clean_metrics.get("nrc_original")
        transaction.nrc_pen = clean_metrics.get("nrc_pen")

        # Cache financial metrics for zero-CPU reads
        transaction.financial_cache = clean_metrics

        return clean_metrics

    # ------------------------------------------------------------------
    # Private helper: hydrate transaction relationships
    # ------------------------------------------------------------------

    def _hydrate_relationships(self, transaction: Transaction) -> None:
        """Load fixed costs and recurring services from repositories."""
        transaction.fixed_costs = self._fc_repo.get_by_transaction(transaction.id)
        transaction.recurring_services = self._rs_repo.get_by_transaction(transaction.id)

    # ------------------------------------------------------------------
    # Public: approve_transaction
    # ------------------------------------------------------------------

    def approve_transaction(
        self,
        transaction_id: str,
        current_user: CurrentUser,
        data_payload: Optional[dict[str, object]] = None,
    ) -> ServiceResult:
        """
        Approves a transaction by updating its status and approval date.

        Immutability Check: Only allows approval if status is 'PENDING'.
        Recalculates financial metrics before approval to ensure the database
        has the latest calculated values (prevents stale data).

        Args:
            transaction_id: The ID of the transaction to approve.
            current_user: The authenticated user performing the approval.
            data_payload: Optional dictionary containing updated transaction data.
                         If provided, updates the transaction before approval.
                         Structure: {'transactions': {...}, 'fixed_costs': [...],
                                     'recurring_services': [...]}

        Returns:
            ServiceResult indicating success or failure.
        """
        try:
            # --- RBAC CHECK: Only FINANCE and ADMIN can approve ---
            if current_user.role not in (UserRole.FINANCE, UserRole.ADMIN):
                return ServiceResult(
                    success=False,
                    error="Only FINANCE or ADMIN users can approve transactions.",
                    status_code=403,
                )

            transaction: Optional[Transaction] = self._tx_repo.get_by_id(transaction_id)
            if not transaction:
                return ServiceResult(
                    success=False,
                    error="Transaction not found.",
                    status_code=404,
                )

            # --- STATE CONSISTENCY CHECK ---
            if transaction.approval_status != ApprovalStatus.PENDING:
                return ServiceResult(
                    success=False,
                    error=(
                        f"Cannot approve transaction. Current status is "
                        f"'{transaction.approval_status}'. Only 'PENDING' "
                        f"transactions can be approved."
                    ),
                    status_code=400,
                )

            # Hydrate relationships
            self._hydrate_relationships(transaction)

            # --- Apply data updates if provided ---
            if data_payload:
                update_result: ServiceResult = self._crud_service.update_transaction_data(
                    transaction, data_payload
                )
                if not update_result.success:
                    return update_result

            # --- Recalculate metrics before approval ---
            try:
                self._recalculate_and_apply_metrics(transaction)
            except Exception as calc_error:
                self._logger.error(
                    "Error recalculating metrics before approval for ID %s: %s",
                    transaction_id,
                    str(calc_error),
                    exc_info=True,
                )
                # Continue with approval even if recalculation fails

            # Update status
            transaction.approval_status = ApprovalStatus.APPROVED
            transaction.approval_date = datetime.now(timezone.utc)

            # Persist via repository
            self._tx_repo.update(transaction)

            # Audit trail
            log_audit_event(
                logger=self._logger,
                action="APPROVE",
                entity_type="Transaction",
                entity_id=transaction_id,
                user_id=current_user.id,
                details={
                    "approved_by": current_user.full_name,
                    "client_name": transaction.client_name,
                },
            )

            # Send approval email (non-blocking)
            try:
                self._email_service.send_status_update_email(transaction, "APPROVED")
            except Exception as email_err:
                self._logger.error(
                    "Transaction approved, but email notification failed: %s",
                    str(email_err),
                )

            return ServiceResult(
                success=True,
                data={"message": "Transaction approved successfully."},
            )
        except Exception as exc:
            self._logger.error(
                "Error during transaction approval for ID %s: %s",
                transaction_id,
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: reject_transaction
    # ------------------------------------------------------------------

    def reject_transaction(
        self,
        transaction_id: str,
        current_user: CurrentUser,
        rejection_note: Optional[str] = None,
        data_payload: Optional[dict[str, object]] = None,
    ) -> ServiceResult:
        """
        Rejects a transaction by updating its status and approval date.

        Immutability Check: Only allows rejection if status is 'PENDING'.
        Recalculates financial metrics before rejection to ensure the database
        has the latest calculated values (prevents stale data).

        Args:
            transaction_id: The ID of the transaction to reject.
            current_user: The authenticated user performing the rejection.
            rejection_note: Optional note explaining the rejection reason.
            data_payload: Optional dictionary containing updated transaction data.
                         If provided, updates the transaction before rejection.
                         Structure: {'transactions': {...}, 'fixed_costs': [...],
                                     'recurring_services': [...]}

        Returns:
            ServiceResult indicating success or failure.
        """
        try:
            # --- RBAC CHECK: Only FINANCE and ADMIN can reject ---
            if current_user.role not in (UserRole.FINANCE, UserRole.ADMIN):
                return ServiceResult(
                    success=False,
                    error="Only FINANCE or ADMIN users can reject transactions.",
                    status_code=403,
                )

            transaction: Optional[Transaction] = self._tx_repo.get_by_id(transaction_id)
            if not transaction:
                return ServiceResult(
                    success=False,
                    error="Transaction not found.",
                    status_code=404,
                )

            # --- STATE CONSISTENCY CHECK ---
            if transaction.approval_status != ApprovalStatus.PENDING:
                return ServiceResult(
                    success=False,
                    error=(
                        f"Cannot reject transaction. Current status is "
                        f"'{transaction.approval_status}'. Only 'PENDING' "
                        f"transactions can be rejected."
                    ),
                    status_code=400,
                )

            # Hydrate relationships
            self._hydrate_relationships(transaction)

            # --- Apply data updates if provided ---
            if data_payload:
                update_result: ServiceResult = self._crud_service.update_transaction_data(
                    transaction, data_payload
                )
                if not update_result.success:
                    return update_result

            # --- Recalculate metrics before rejection ---
            try:
                self._recalculate_and_apply_metrics(transaction)
            except Exception as calc_error:
                self._logger.error(
                    "Error recalculating metrics before rejection for ID %s: %s",
                    transaction_id,
                    str(calc_error),
                    exc_info=True,
                )
                # Continue with rejection even if recalculation fails

            # Update status
            transaction.approval_status = ApprovalStatus.REJECTED
            transaction.approval_date = datetime.now(timezone.utc)

            # Store rejection note if provided
            if rejection_note:
                transaction.rejection_note = rejection_note.strip()

            # Persist via repository
            self._tx_repo.update(transaction)

            # Audit trail
            log_audit_event(
                logger=self._logger,
                action="REJECT",
                entity_type="Transaction",
                entity_id=transaction_id,
                user_id=current_user.id,
                details={
                    "rejected_by": current_user.full_name,
                    "client_name": transaction.client_name,
                    "rejection_note": rejection_note or "",
                },
            )

            # Send rejection email (non-blocking)
            try:
                self._email_service.send_status_update_email(transaction, "REJECTED")
            except Exception as email_err:
                self._logger.error(
                    "Transaction rejected, but email notification failed: %s",
                    str(email_err),
                )

            return ServiceResult(
                success=True,
                data={"message": "Transaction rejected successfully."},
            )
        except Exception as exc:
            self._logger.error(
                "Error during transaction rejection for ID %s: %s",
                transaction_id,
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: recalculate_commission_and_metrics
    # ------------------------------------------------------------------

    def recalculate_commission_and_metrics(
        self,
        transaction_id: str,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Applies the official commission, recalculates all financial metrics,
        and saves the updated Transaction object.

        IMMUTABILITY CHECK: Only allows modification if status is 'PENDING'.

        Uses the stateless calculate_financial_metrics function as the single
        source of truth for calculations.

        Args:
            transaction_id: The ID of the transaction to recalculate.
            current_user: The authenticated user triggering the recalculation.

        Returns:
            ServiceResult with the full, updated transaction details.
        """
        try:
            # --- RBAC CHECK: Only FINANCE and ADMIN can recalculate ---
            if current_user.role not in (UserRole.FINANCE, UserRole.ADMIN):
                return ServiceResult(
                    success=False,
                    error="Only FINANCE or ADMIN users can recalculate transactions.",
                    status_code=403,
                )

            # 1. Retrieve the transaction object
            transaction: Optional[Transaction] = self._tx_repo.get_by_id(transaction_id)
            if not transaction:
                return ServiceResult(
                    success=False,
                    error="Transaction not found.",
                    status_code=404,
                )

            # --- IMMUTABILITY CHECK ---
            if transaction.approval_status != ApprovalStatus.PENDING:
                return ServiceResult(
                    success=False,
                    error=(
                        f"Transaction is already {transaction.approval_status}. "
                        f"Financial metrics can only be modified for 'PENDING' transactions."
                    ),
                    status_code=403,
                )

            # Hydrate relationships
            self._hydrate_relationships(transaction)

            # 2. Recalculate all metrics (VAN, TIR, Commission, etc.)
            self._recalculate_and_apply_metrics(transaction)

            # 3. Persist changes via repository
            self._tx_repo.update(transaction)

            # 4. Audit trail
            log_audit_event(
                logger=self._logger,
                action="RECALCULATE",
                entity_type="Transaction",
                entity_id=transaction_id,
                user_id=current_user.id,
                details={"recalculated_by": current_user.full_name},
            )

            # 5. Return the full, updated transaction details
            return self._crud_service.get_transaction_detail(transaction_id, current_user)

        except Exception as exc:
            self._logger.error(
                "Error during commission recalculation for ID %s: %s",
                transaction_id,
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Error during commission recalculation: {str(exc)}",
                status_code=500,
            )
