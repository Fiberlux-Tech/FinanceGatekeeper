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

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models.user import User
from app.logger import StructuredLogger
from app.models.enums import ApprovalStatus, BusinessUnit, UserRole
from app.models.service_models import ServiceResult
from app.models.transaction import Transaction
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.base_service import BaseService
from app.services.email_service import EmailService
from app.services.file_archival import FileArchivalService
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
        file_archival: FileArchivalService,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._tx_repo = transaction_repo
        self._fc_repo = fixed_cost_repo
        self._rs_repo = recurring_service_repo
        self._email_service = email_service
        self._crud_service = crud_service
        self._file_archival = file_archival

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
        # Recalculate financial metrics
        financial_metrics: dict[str, object] = calculate_financial_metrics(
            transaction.to_financial_engine_dict(),
        ).model_dump()
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
        current_user: User,
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
                return ServiceResult(
                    success=False,
                    error="Cannot approve: financial metric recalculation failed. Please retry or contact support.",
                    status_code=500,
                )

            # Update status
            transaction.approval_status = ApprovalStatus.APPROVED
            transaction.approval_date = datetime.now(timezone.utc)

            # Persist via repository
            self._tx_repo.update(transaction)

            # Audit trail (dual: log + SQLite)
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
                conn=self._tx_repo.sqlite,
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
        current_user: User,
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
                return ServiceResult(
                    success=False,
                    error="Cannot reject: financial metric recalculation failed. Please retry or contact support.",
                    status_code=500,
                )

            # Update status
            transaction.approval_status = ApprovalStatus.REJECTED
            transaction.approval_date = datetime.now(timezone.utc)

            # Store rejection note if provided
            if rejection_note:
                transaction.rejection_note = rejection_note.strip()

            # Persist via repository
            self._tx_repo.update(transaction)

            # Audit trail (dual: log + SQLite)
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
                conn=self._tx_repo.sqlite,
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
        current_user: User,
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

            # 4. Audit trail (dual: log + SQLite)
            log_audit_event(
                logger=self._logger,
                action="RECALCULATE",
                entity_type="Transaction",
                entity_id=transaction_id,
                user_id=current_user.id,
                details={"recalculated_by": current_user.full_name},
                conn=self._tx_repo.sqlite,
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

    # ------------------------------------------------------------------
    # Public: approve_transaction_with_archival
    # ------------------------------------------------------------------

    def approve_transaction_with_archival(
        self,
        transaction_id: str,
        current_user: User,
        source_file_path: Path,
        business_unit: BusinessUnit,
        expected_sha256: str,
        data_payload: Optional[dict[str, object]] = None,
    ) -> ServiceResult:
        """Approve a transaction and archive the source file.

        Orchestrates the full approval workflow (M5 — archival-first):
        1. File archival (verify hash → rename → encrypt → move)
        2. DB approval (status change, metrics recalculation, audit log)

        File archival runs first so that if it fails, the DB status
        remains PENDING and the system is consistent.  If DB approval
        fails after a successful archival, a warning is logged — the
        file has already moved but the transaction stays PENDING.

        Parameters
        ----------
        transaction_id:
            The ID of the transaction to approve.
        current_user:
            The authenticated user performing the approval.
        source_file_path:
            Absolute path to the ``.xlsx`` file in the inbox.
        business_unit:
            Business unit for archive folder routing.
        expected_sha256:
            SHA-256 hash from the card scan — used for chain-of-custody
            verification before the file is moved.
        data_payload:
            Optional updated transaction data to apply before approval.

        Returns
        -------
        ServiceResult
            On success, ``data`` contains ``{'message': str,
            'archived_path': str | None, 'sha256': str | None}``.
        """
        # Step 1: File archival (hash verify → rename → encrypt → move)
        archival_result = self._file_archival.archive_approved(
            source_path=source_file_path,
            transaction_id=transaction_id,
            business_unit=business_unit,
            expected_sha256=expected_sha256,
        )

        if not archival_result.success:
            return ServiceResult(
                success=False,
                error=(
                    f"File archival failed — transaction remains PENDING. "
                    f"Detail: {archival_result.error}"
                ),
                status_code=archival_result.status_code,
            )

        archived_path: Optional[str] = None
        sha256: Optional[str] = None
        if isinstance(archival_result.data, dict):
            archived_path = archival_result.data.get("archived_path")
            sha256 = archival_result.data.get("sha256")

        # Step 2: DB approval (RBAC, state check, metrics, persist, audit, email)
        db_result = self.approve_transaction(
            transaction_id=transaction_id,
            current_user=current_user,
            data_payload=data_payload,
        )
        if not db_result.success:
            self._logger.warning(
                "File for transaction %s archived to %s, but DB approval "
                "failed: %s. Manual DB update may be required.",
                transaction_id,
                archived_path,
                db_result.error,
            )
            return db_result

        return ServiceResult(
            success=True,
            data={
                "message": "Transaction approved successfully.",
                "archived_path": archived_path,
                "sha256": sha256,
            },
        )

    # ------------------------------------------------------------------
    # Public: reject_transaction_with_archival
    # ------------------------------------------------------------------

    def reject_transaction_with_archival(
        self,
        transaction_id: str,
        current_user: User,
        rejection_note: str,
        source_file_path: Path,
        business_unit: BusinessUnit,
        expected_sha256: str,
        data_payload: Optional[dict[str, object]] = None,
    ) -> ServiceResult:
        """Reject a transaction and archive the source file.

        Orchestrates the full rejection workflow (M5 — archival-first):
        1. File archival (verify hash → rename → move to rejected archive)
        2. DB rejection (status change, rejection note, audit log, email)

        File archival runs first so that if it fails, the DB status
        remains PENDING and the system is consistent.  If DB rejection
        fails after a successful archival, a warning is logged — the
        file has already moved but the transaction stays PENDING.

        Parameters
        ----------
        transaction_id:
            The ID of the transaction to reject.
        current_user:
            The authenticated user performing the rejection.
        rejection_note:
            Reason for rejection (stored in DB and included in email).
        source_file_path:
            Absolute path to the ``.xlsx`` file in the inbox.
        business_unit:
            Business unit for archive folder routing.
        expected_sha256:
            SHA-256 hash from the card scan — used for chain-of-custody
            verification before the file is moved.
        data_payload:
            Optional updated transaction data to apply before rejection.

        Returns
        -------
        ServiceResult
            On success, ``data`` contains ``{'message': str,
            'archived_path': str | None, 'sha256': str | None}``.
        """
        # Step 1: File archival (hash verify → rename → move — no encryption)
        archival_result = self._file_archival.archive_rejected(
            source_path=source_file_path,
            transaction_id=transaction_id,
            business_unit=business_unit,
            expected_sha256=expected_sha256,
        )

        if not archival_result.success:
            return ServiceResult(
                success=False,
                error=(
                    f"File archival failed — transaction remains PENDING. "
                    f"Detail: {archival_result.error}"
                ),
                status_code=archival_result.status_code,
            )

        archived_path: Optional[str] = None
        sha256: Optional[str] = None
        if isinstance(archival_result.data, dict):
            archived_path = archival_result.data.get("archived_path")
            sha256 = archival_result.data.get("sha256")

        # Step 2: DB rejection (RBAC, state check, metrics, persist, audit, email)
        db_result = self.reject_transaction(
            transaction_id=transaction_id,
            current_user=current_user,
            rejection_note=rejection_note,
            data_payload=data_payload,
        )
        if not db_result.success:
            self._logger.warning(
                "File for transaction %s archived to %s, but DB rejection "
                "failed: %s. Manual DB update may be required.",
                transaction_id,
                archived_path,
                db_result.error,
            )
            return db_result

        return ServiceResult(
            success=True,
            data={
                "message": "Transaction rejected successfully.",
                "archived_path": archived_path,
                "sha256": sha256,
            },
        )
