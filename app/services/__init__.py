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

from typing import Optional, TypedDict

from app.auth import SessionManager
from app.config import AppConfig
from app.database import DatabaseManager
from app.logger import get_logger
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.master_variable_repository import MasterVariableRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.services.app_settings_service import AppSettingsService
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.services.excel_parser import ExcelParserService
from app.services.file_guards import FileGuardsService
from app.services.file_watcher import FileWatcherService
from app.services.jit_provisioning import JITProvisioningService
from app.services.kpi import KPIService
from app.services.native_opener import NativeOpenerService
from app.services.path_discovery import PathDiscoveryService
from app.services.transaction_crud import TransactionCrudService
from app.services.transaction_preview import TransactionPreviewService
from app.services.transaction_workflow import TransactionWorkflowService
from app.services.session_cache import SessionCacheService
from app.services.users import UserService
from app.services.variables import VariableService


class ServiceContainer(TypedDict, total=False):
    """Typed container for all application services.

    Services marked ``Optional`` may be ``None`` when the required
    infrastructure (e.g. SharePoint sync folder) is unavailable.
    """

    # --- Core (always present) ---
    auth_service: AuthService
    variable_service: VariableService
    user_service: UserService
    jit_provisioning_service: JITProvisioningService
    kpi_service: KPIService
    email_service: EmailService
    excel_parser_service: ExcelParserService
    transaction_crud_service: TransactionCrudService
    transaction_workflow_service: TransactionWorkflowService
    transaction_preview_service: TransactionPreviewService

    # --- Infrastructure ---
    app_settings_service: AppSettingsService

    # --- Phase 2: Observer & Native Interaction ---
    path_discovery_service: PathDiscoveryService
    file_guards_service: FileGuardsService
    native_opener_service: NativeOpenerService
    file_watcher_service: Optional[FileWatcherService]


def create_services(
    db: DatabaseManager,
    config: AppConfig,
    session: SessionManager,
    session_cache: SessionCacheService,
) -> ServiceContainer:
    """
    Wire all repositories and services together.

    This is the single composition root for the service layer.  The
    application entry-point calls this once at startup and passes the
    returned dict to views / commands as needed.

    Args:
        db: Initialised DatabaseManager with Supabase + SQLite ready.
        config: Application configuration (injected into services that need it).

    Returns:
        ServiceContainer mapping service names to fully-wired instances.
    """
    logger = get_logger("services")

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
        config=config,
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
        config=config,
        logger=logger,
    )
    transaction_preview_service = TransactionPreviewService(
        logger=logger,
    )

    auth_service = AuthService(
        db=db,
        session=session,
        jit_service=jit_provisioning_service,
        session_cache=session_cache,
        logger=logger,
        user_repo=user_repo,
    )

    # ------------------------------------------------------------------
    # 3. Orchestration services (depend on other services)
    # ------------------------------------------------------------------
    excel_parser_service = ExcelParserService(
        variable_service=variable_service,
        config=config,
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

    # ------------------------------------------------------------------
    # 4. Infrastructure — persistent app settings
    # ------------------------------------------------------------------
    app_settings_service = AppSettingsService(db=db, logger=logger)

    # ------------------------------------------------------------------
    # 5. Phase 2 — Observer & Native Interaction services
    # ------------------------------------------------------------------
    path_discovery_service = PathDiscoveryService(config=config, logger=logger)
    file_guards_service = FileGuardsService(config=config, logger=logger)
    native_opener_service = NativeOpenerService(logger=logger)

    file_watcher_service: Optional[FileWatcherService] = None
    try:
        stored_root = app_settings_service.get_sharepoint_root()
        resolved_paths = path_discovery_service.resolve(stored_root=stored_root)
        file_watcher_service = FileWatcherService(
            inbox_path=resolved_paths.inbox,
            file_guards=file_guards_service,
            config=config,
            logger=logger,
        )
    except FileNotFoundError as exc:
        logger.warning(
            "SharePoint path not found — file watcher disabled: %s", exc,
        )

    return ServiceContainer(
        auth_service=auth_service,
        variable_service=variable_service,
        user_service=user_service,
        jit_provisioning_service=jit_provisioning_service,
        kpi_service=kpi_service,
        email_service=email_service,
        excel_parser_service=excel_parser_service,
        transaction_crud_service=transaction_crud_service,
        transaction_workflow_service=transaction_workflow_service,
        transaction_preview_service=transaction_preview_service,
        app_settings_service=app_settings_service,
        path_discovery_service=path_discovery_service,
        file_guards_service=file_guards_service,
        native_opener_service=native_opener_service,
        file_watcher_service=file_watcher_service,
    )
