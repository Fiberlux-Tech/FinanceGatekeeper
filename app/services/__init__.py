"""
Business Logic Services Package.

Contains all service modules ported from the legacy Flask application.
Services depend on the Repository layer for data access and the
Auth module for user context.

The ``create_services()`` factory wires every repository and service together,
returning a typed dict that the application layer (commands / views) can
consume without knowing the internal dependency graph.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from app.database import DatabaseManager
from app.logger import get_logger
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.master_variable_repository import MasterVariableRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.services.email_service import EmailService
from app.services.excel_parser import ExcelParserService
from app.services.jit_provisioning import JITProvisioningService
from app.services.kpi import KPIService
from app.services.transaction_crud import TransactionCrudService
from app.services.transaction_preview import TransactionPreviewService
from app.services.transaction_workflow import TransactionWorkflowService
from app.services.users import UserService
from app.services.variables import VariableService


class ServiceContainer(TypedDict):
    """Typed container for all application services."""

    variable_service: VariableService
    user_service: UserService
    jit_provisioning_service: JITProvisioningService
    kpi_service: KPIService
    email_service: EmailService
    excel_parser_service: ExcelParserService
    transaction_crud_service: TransactionCrudService
    transaction_workflow_service: TransactionWorkflowService
    transaction_preview_service: TransactionPreviewService


def create_services(db: DatabaseManager) -> ServiceContainer:
    """
    Wire all repositories and services together.

    This is the single composition root for the service layer.  The
    application entry-point calls this once at startup and passes the
    returned dict to views / commands as needed.

    Args:
        db: Initialised DatabaseManager with Supabase + SQLite ready.

    Returns:
        ServiceContainer mapping service names to fully-wired instances.
    """
    logger: logging.Logger = get_logger("services")

    # ------------------------------------------------------------------
    # 1. Repositories (data-access layer)
    # ------------------------------------------------------------------
    user_repo = UserRepository(db=db, logger=logger)
    variable_repo = MasterVariableRepository(db=db, logger=logger)
    transaction_repo = TransactionRepository(db=db, logger=logger)
    fixed_cost_repo = FixedCostRepository(db=db, logger=logger)
    recurring_service_repo = RecurringServiceRepository(db=db, logger=logger)

    # ------------------------------------------------------------------
    # 2. Leaf services (no service dependencies)
    # ------------------------------------------------------------------
    variable_service = VariableService(
        repo=variable_repo,
        logger=logger,
    )
    user_service = UserService(
        repo=user_repo,
        db=db,
        logger=logger,
    )
    jit_provisioning_service = JITProvisioningService(
        repo=user_repo,
        logger=logger,
    )
    kpi_service = KPIService(
        repo=transaction_repo,
        logger=logger,
    )
    email_service = EmailService(
        user_repo=user_repo,
        logger=logger,
    )
    transaction_preview_service = TransactionPreviewService(
        logger=logger,
    )

    # ------------------------------------------------------------------
    # 3. Orchestration services (depend on other services)
    # ------------------------------------------------------------------
    excel_parser_service = ExcelParserService(
        variable_service=variable_service,
        logger=logger,
    )
    transaction_crud_service = TransactionCrudService(
        transaction_repo=transaction_repo,
        fixed_cost_repo=fixed_cost_repo,
        recurring_service_repo=recurring_service_repo,
        email_service=email_service,
        variable_service=variable_service,
        logger=logger,
    )
    transaction_workflow_service = TransactionWorkflowService(
        transaction_repo=transaction_repo,
        fixed_cost_repo=fixed_cost_repo,
        recurring_service_repo=recurring_service_repo,
        email_service=email_service,
        crud_service=transaction_crud_service,
        logger=logger,
    )

    return ServiceContainer(
        variable_service=variable_service,
        user_service=user_service,
        jit_provisioning_service=jit_provisioning_service,
        kpi_service=kpi_service,
        email_service=email_service,
        excel_parser_service=excel_parser_service,
        transaction_crud_service=transaction_crud_service,
        transaction_workflow_service=transaction_workflow_service,
        transaction_preview_service=transaction_preview_service,
    )
