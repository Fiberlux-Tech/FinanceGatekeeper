"""
Master Variable Service.

Manages retrieval and RBAC-enforced updates of system-wide master variables
(exchange rates, cost of capital, etc.). Variables are append-only to preserve
a full audit trail.

Refactored from legacy Flask service:
    - Stripped: current_app, g, @require_jwt, db.session, SQLAlchemy ORM
    - Injected: MasterVariableRepository, logging.Logger
    - All methods return ServiceResult for consistent contract with view layer
"""

from __future__ import annotations

from typing import Optional

from app.auth import CurrentUser
from app.config import AppConfig
from app.logger import StructuredLogger
from app.models.enums import UserRole
from app.models.master_variable import MasterVariable
from app.models.service_models import ServiceResult
from app.repositories.master_variable_repository import MasterVariableRepository
from app.services.base_service import BaseService
from app.utils.audit import log_audit_event


class VariableService(BaseService):
    """
    Service layer for master variable operations.

    Handles retrieval (all users) and RBAC-enforced updates (role-gated).
    Delegates all data access to MasterVariableRepository.
    """

    def __init__(
        self,
        repo: MasterVariableRepository,
        config: AppConfig,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._repo = repo
        self._config = config

    def get_all_master_variables(
        self,
        category: Optional[str] = None,
    ) -> ServiceResult:
        """
        Retrieve all master variable records, optionally filtered by category.

        Supports the "everyone can view" requirement -- no RBAC enforcement
        on reads. Category is uppercased for consistent matching.

        Args:
            category: Optional category filter (e.g. "RATES"). Uppercased automatically.

        Returns:
            ServiceResult with data containing a list of variable dicts on success,
            or an error message on failure.
        """
        try:
            normalized_category: Optional[str] = (
                category.upper() if category else None
            )
            variables: list[MasterVariable] = self._repo.get_all(
                category=normalized_category,
            )
            return ServiceResult(
                success=True,
                data=[v.model_dump() for v in variables],
            )
        except Exception as exc:
            self._logger.error(
                "Failed to fetch master variables: %s", exc, exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error fetching master variables: {exc}",
                status_code=500,
            )

    def update_master_variable(
        self,
        variable_name: str,
        value: str,
        comment: str,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Insert a new record for a master variable, enforcing RBAC.

        Validates that:
            1. The variable name is registered in AppConfig.MASTER_VARIABLE_ROLES.
            2. The value is a valid number.
            3. The current user has the required role (or is ADMIN).

        On success, creates the record via the repository and logs a structured
        audit event.

        Args:
            variable_name: Registered variable identifier (e.g. "tipoCambio").
            value: Numeric value as a string (coerced to float internally).
            comment: User-provided justification for the change.
            current_user: The authenticated user performing the update.

        Returns:
            ServiceResult with a success message or an appropriate error.
        """
        variable_config: Optional[dict[str, str]] = (
            self._config.MASTER_VARIABLE_ROLES.get(variable_name)
        )

        # 1. Validate that the variable name is registered
        if not variable_config:
            return ServiceResult(
                success=False,
                error=(
                    f"Variable name '{variable_name}' is not a registered "
                    f"master variable."
                ),
                status_code=400,
            )

        # 2. Validate that the value is numeric
        try:
            numeric_value: float = float(value)
        except (TypeError, ValueError):
            return ServiceResult(
                success=False,
                error="Variable value must be a valid number.",
                status_code=400,
            )

        # 3. RBAC enforcement
        required_role: str = variable_config["write_role"]
        variable_category: str = variable_config["category"]

        if (
            current_user.role != UserRole.ADMIN
            and current_user.role != required_role
        ):
            return ServiceResult(
                success=False,
                error=(
                    f"Permission denied. Only {required_role} can update "
                    f"the {variable_category} category."
                ),
                status_code=403,
            )

        # 4. Persist the new variable record
        try:
            new_variable = MasterVariable(
                variable_name=variable_name,
                variable_value=numeric_value,
                category=variable_category,
                user_id=current_user.id,
                comment=comment,
            )
            created: MasterVariable = self._repo.create(new_variable)

            # 5. Audit trail (structured JSON log)
            log_audit_event(
                logger=self._logger,
                action="UPDATE_VARIABLE",
                entity_type="MasterVariable",
                entity_id=variable_name,
                user_id=current_user.id,
                details={
                    "new_value": numeric_value,
                    "category": variable_category,
                    "comment": comment,
                },
            )

            return ServiceResult(
                success=True,
                data={
                    "message": (
                        f"Successfully updated {variable_name} "
                        f"to {numeric_value}."
                    ),
                    "variable": created.model_dump(),
                },
            )
        except Exception as exc:
            self._logger.error(
                "Failed to save master variable '%s': %s",
                variable_name,
                exc,
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Database error saving variable: {exc}",
                status_code=500,
            )

    def get_latest_master_variables(
        self,
        variable_names: list[str],
    ) -> dict[str, Optional[float]]:
        """
        Retrieve the most recent value for each requested variable name.

        This is a pure data-retrieval method used internally by the financial
        engine during transaction creation. It does NOT return ServiceResult
        because it is consumed by other services, not the view layer.

        Args:
            variable_names: List of variable identifiers to look up.

        Returns:
            Dict mapping each variable name to its latest float value,
            or None if no historical record exists.
        """
        if not variable_names:
            return {}
        return self._repo.get_latest(variable_names)
