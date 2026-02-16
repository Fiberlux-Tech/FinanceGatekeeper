"""
Transaction CRUD Service.

Handles creation, retrieval, update, and template generation for transactions.
Refactored from legacy transactions.py -- class-based, repository-backed,
with structured audit logging and ServiceResult envelope.

Functions ported:
    - save_transaction
    - get_transactions
    - get_transaction_details (renamed from get_transaction_details)
    - update_transaction_content
    - get_transaction_template
    - _generate_unique_id (static helper)
    - update_transaction_data (public helper, used cross-service)
"""

from __future__ import annotations

import traceback
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.models.user import User
from app.logger import StructuredLogger
from app.models.enums import ApprovalStatus, UserRole
from app.models.fixed_cost import FixedCost
from app.models.recurring_service import RecurringService
from app.models.service_models import ServiceResult
from app.models.transaction import Transaction
from app.repositories.fixed_cost_repository import FixedCostRepository
from app.repositories.recurring_service_repository import RecurringServiceRepository
from app.repositories.transaction_repository import (
    PaginatedTransactions,
    TransactionRepository,
)
from app.services.base_service import BaseService
from app.services.email_service import EmailService
from app.services.financial_engine import (
    CurrencyConverter,
    calculate_financial_metrics,
    initialize_timeline,
)
from app.services.variables import VariableService
from app.utils.audit import log_audit_event
from app.utils.general import convert_to_json_safe
from app.utils.string_helpers import normalize_keys


def _generate_unique_id() -> str:
    """
    Generates a unique transaction ID using microseconds for maximum granularity.

    Format: FLX{YY}-{MMDDHHMMSSFFFFF}

    Returns:
        A unique transaction ID string.
    """
    now = datetime.now()
    year_part: str = now.strftime("%y")
    datetime_micro_part: str = now.strftime("%m%d%H%M%S%f")
    return f"FLX{year_part}-{datetime_micro_part}"


class TransactionCrudService(BaseService):
    """
    Service handling transaction CRUD operations: create, read, update,
    template generation.

    Dependencies are injected via __init__ -- no global state, no Flask.
    """

    def __init__(
        self,
        transaction_repo: TransactionRepository,
        fixed_cost_repo: FixedCostRepository,
        recurring_service_repo: RecurringServiceRepository,
        email_service: EmailService,
        variable_service: VariableService,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._tx_repo = transaction_repo
        self._fc_repo = fixed_cost_repo
        self._rs_repo = recurring_service_repo
        self._email_service = email_service
        self._variable_service = variable_service

    # ------------------------------------------------------------------
    # Private static: enrich recurring service PEN fields
    # ------------------------------------------------------------------

    @staticmethod
    def _enrich_recurring_service_pen_fields(
        service_item: dict[str, object],
        converter: CurrencyConverter,
    ) -> None:
        """Populate ``price_pen``, ``cost_unit_1_pen``, ``cost_unit_2_pen`` in-place.

        Applies currency conversion when the PEN fields are missing, zero,
        or empty.  Mutates *service_item* directly.

        Args:
            service_item: A single recurring service dict from the payload.
            converter: ``CurrencyConverter`` with the active exchange rate.
        """
        if service_item.get("price_pen") in [0, Decimal("0"), None, ""]:
            price_original: Decimal = service_item.get("price_original", Decimal("0"))
            price_currency: str = service_item.get("price_currency", "PEN")
            service_item["price_pen"] = converter.to_pen(price_original, price_currency)

        if service_item.get("cost_unit_1_pen") in [0, Decimal("0"), None, ""]:
            cost_unit_1_original: Decimal = service_item.get("cost_unit_1_original", Decimal("0"))
            cost_unit_currency: str = service_item.get("cost_unit_currency", "USD")
            service_item["cost_unit_1_pen"] = converter.to_pen(cost_unit_1_original, cost_unit_currency)

        if service_item.get("cost_unit_2_pen") in [0, Decimal("0"), None, ""]:
            cost_unit_2_original: Decimal = service_item.get("cost_unit_2_original", Decimal("0"))
            cost_unit_currency_2: str = service_item.get("cost_unit_currency", "USD")
            service_item["cost_unit_2_pen"] = converter.to_pen(cost_unit_2_original, cost_unit_currency_2)

    # ------------------------------------------------------------------
    # Public helper: update transaction data (scalar + relationships)
    # ------------------------------------------------------------------

    def update_transaction_data(
        self,
        transaction: Transaction,
        data_payload: dict[str, object],
    ) -> ServiceResult:
        """
        Central helper to update a transaction's scalar fields and relationships.

        This method:
        1. Updates scalar fields (MRC, Unit, Contract Term, etc.) on the model.
        2. Replaces FixedCost and RecurringService records via repositories.
        3. Recalculates all financial metrics (VAN, TIR, Commissions).
        4. Does NOT change the transaction status or ID.

        Args:
            transaction: The Transaction object to update.
            data_payload: Dictionary containing updated transaction data with structure:
                {
                    'transactions': {...},
                    'fixed_costs': [...],
                    'recurring_services': [...]
                }

        Returns:
            ServiceResult indicating success or failure.
        """
        try:
            tx_data: dict[str, object] = data_payload.get("transactions", {})
            fixed_costs_data: list[dict[str, object]] = data_payload.get("fixed_costs", [])
            recurring_services_data: list[dict[str, object]] = data_payload.get("recurring_services", [])

            # 1. Update scalar fields on the transaction model
            # NOTE: tipoCambio, costoCapitalAnual, tasaCartaFianza are EXCLUDED
            # These rates are frozen at transaction creation and cannot be modified.
            # See: master_variables_snapshot for audit trail.
            updatable_fields: list[str] = [
                "unidad_negocio", "client_name", "company_id", "order_id",
                "mrc_currency", "nrc_currency",
                "plazo_contrato", "aplica_carta_fianza",
                "gigalan_region", "gigalan_sale_type", "gigalan_old_mrc",
            ]

            for field in updatable_fields:
                if field in tx_data:
                    setattr(transaction, field, tx_data[field])

            # 2. Replace FixedCost records via repository
            new_fixed_costs: list[FixedCost] = []
            for cost_item in fixed_costs_data:
                new_cost = FixedCost(
                    transaction_id=transaction.id,
                    categoria=cost_item.get("categoria"),
                    tipo_servicio=cost_item.get("tipo_servicio"),
                    ticket=cost_item.get("ticket"),
                    ubicacion=cost_item.get("ubicacion"),
                    cantidad=cost_item.get("cantidad"),
                    costo_unitario_original=cost_item.get("costo_unitario_original"),
                    costo_unitario_currency=cost_item.get("costo_unitario_currency", "USD"),
                    costo_unitario_pen=cost_item.get("costo_unitario_pen"),
                    periodo_inicio=cost_item.get("periodo_inicio", 0),
                    duracion_meses=cost_item.get("duracion_meses", 1),
                )
                new_fixed_costs.append(new_cost)

            # 3. Replace RecurringService records via repository
            converter = CurrencyConverter(transaction.tipo_cambio or 1)
            new_recurring_services: list[RecurringService] = []

            for service_item in recurring_services_data:
                self._enrich_recurring_service_pen_fields(service_item, converter)

                new_service = RecurringService(
                    transaction_id=transaction.id,
                    tipo_servicio=service_item.get("tipo_servicio"),
                    nota=service_item.get("nota"),
                    ubicacion=service_item.get("ubicacion"),
                    quantity=service_item.get("quantity"),
                    price_original=service_item.get("price_original"),
                    price_currency=service_item.get("price_currency", "PEN"),
                    price_pen=service_item.get("price_pen"),
                    cost_unit_1_original=service_item.get("cost_unit_1_original"),
                    cost_unit_2_original=service_item.get("cost_unit_2_original"),
                    cost_unit_currency=service_item.get("cost_unit_currency", "USD"),
                    cost_unit_1_pen=service_item.get("cost_unit_1_pen"),
                    cost_unit_2_pen=service_item.get("cost_unit_2_pen"),
                    proveedor=service_item.get("proveedor"),
                )
                new_recurring_services.append(new_service)

            # Atomic replace: both detail tables in a single SQLite transaction (M4)
            with self._fc_repo._db.batch_write():
                self._fc_repo.replace_for_transaction(transaction.id, new_fixed_costs)
                self._rs_repo.replace_for_transaction(transaction.id, new_recurring_services)

            # 4. Update transaction relationships in memory for recalculation
            transaction.fixed_costs = new_fixed_costs
            transaction.recurring_services = new_recurring_services

            # 5. Recalculate financial metrics based on new values
            financial_metrics: dict[str, object] = calculate_financial_metrics(
                transaction.to_financial_engine_dict(),
            ).model_dump()
            clean_metrics: dict[str, object] = convert_to_json_safe(financial_metrics)

            # 6. Update transaction with fresh calculations
            for key, value in clean_metrics.items():
                if hasattr(transaction, key):
                    setattr(transaction, key, value)

            transaction.costo_instalacion = clean_metrics.get("costo_instalacion")
            transaction.mrc_original = clean_metrics.get("mrc_original")
            transaction.mrc_pen = clean_metrics.get("mrc_pen")
            transaction.nrc_original = clean_metrics.get("nrc_original")
            transaction.nrc_pen = clean_metrics.get("nrc_pen")

            # CACHE: Update cached metrics so reads are zero-CPU
            transaction.financial_cache = clean_metrics

            return ServiceResult(success=True)

        except Exception as exc:
            self._logger.error(
                "Error during transaction update: %s\n%s",
                str(exc),
                traceback.format_exc(),
            )
            return ServiceResult(
                success=False,
                error=f"Error updating transaction: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: save_transaction
    # ------------------------------------------------------------------

    def save_transaction(
        self,
        data: dict[str, object],
        current_user: User,
    ) -> ServiceResult:
        """
        Saves a new transaction and its related costs to the database.

        Overwrites the 'salesman' field with the currently logged-in user's full_name.
        Recalculates financial metrics on the backend to ensure the database always
        has correct calculated values (prevents frontend calculation errors).

        Args:
            data: The full transaction payload with keys:
                  'transactions', 'fixed_costs', 'recurring_services'.
            current_user: The authenticated user submitting the transaction.

        Returns:
            ServiceResult with the new transaction_id on success.
        """
        try:
            tx_data: dict[str, object] = data.get("transactions", {})

            # --- Validation: require unidad_negocio ---
            unidad_de_negocio: Optional[str] = tx_data.get("unidad_negocio")
            if not unidad_de_negocio or str(unidad_de_negocio).strip() == "":
                return ServiceResult(
                    success=False,
                    error="La 'Unidad de Negocio' es obligatoria. No se puede guardar la transaccion.",
                    status_code=400,
                )

            # --- Salesman overwrite: use authenticated user ---
            tx_data["salesman"] = current_user.full_name

            # --- Recalculate metrics on backend ---
            clean_metrics: dict[str, object] = {}
            try:
                full_data_package: dict[str, object] = {
                    **tx_data,
                    "fixed_costs": data.get("fixed_costs", []),
                    "recurring_services": data.get("recurring_services", []),
                }

                # Recalculate all financial metrics using backend logic
                recalculated_metrics: dict[str, object] = calculate_financial_metrics(
                    full_data_package,
                ).model_dump()
                clean_metrics = convert_to_json_safe(recalculated_metrics)

                # Override frontend values with backend calculations
                tx_data.update(clean_metrics)
            except Exception as calc_error:
                self._logger.error(
                    "Error calculating metrics during save: %s",
                    str(calc_error),
                    exc_info=True,
                )
                # If calculation fails, continue with frontend values (log warning)
                self._logger.warning(
                    "Falling back to frontend-provided values for transaction"
                )

            unique_id: str = _generate_unique_id()

            # --- Extract GIGALAN Data ---
            gigalan_region: Optional[str] = tx_data.get("gigalan_region")
            gigalan_sale_type: Optional[str] = tx_data.get("gigalan_sale_type")
            gigalan_old_mrc: Optional[float] = tx_data.get("gigalan_old_mrc")

            # Create the main Transaction object
            new_transaction = Transaction(
                id=unique_id,
                created_by=current_user.id,
                unidad_negocio=tx_data.get("unidad_negocio", ""),
                client_name=tx_data.get("client_name", ""),
                company_id=tx_data.get("company_id"),
                salesman=tx_data["salesman"],
                order_id=tx_data.get("order_id"),
                tipo_cambio=tx_data.get("tipo_cambio"),
                # MRC / NRC
                mrc_original=tx_data.get("mrc_original"),
                mrc_currency=tx_data.get("mrc_currency", "PEN"),
                mrc_pen=tx_data.get("mrc_pen"),
                nrc_original=tx_data.get("nrc_original"),
                nrc_currency=tx_data.get("nrc_currency", "PEN"),
                nrc_pen=tx_data.get("nrc_pen"),
                # KPIs (all in PEN)
                van=tx_data.get("van"),
                tir=tx_data.get("tir"),
                payback=tx_data.get("payback"),
                total_revenue=tx_data.get("total_revenue"),
                total_expense=tx_data.get("total_expense"),
                comisiones=tx_data.get("comisiones"),
                comisiones_rate=tx_data.get("comisiones_rate"),
                costo_instalacion=tx_data.get("costo_instalacion"),
                costo_instalacion_ratio=tx_data.get("costo_instalacion_ratio"),
                gross_margin=tx_data.get("gross_margin"),
                gross_margin_ratio=tx_data.get("gross_margin_ratio"),
                plazo_contrato=tx_data.get("plazo_contrato"),
                costo_capital_anual=tx_data.get("costo_capital_anual"),
                tasa_carta_fianza=tx_data.get("tasa_carta_fianza"),
                costo_carta_fianza=tx_data.get("costo_carta_fianza"),
                aplica_carta_fianza=tx_data.get("aplica_carta_fianza", False),
                # GIGALAN fields
                gigalan_region=gigalan_region,
                gigalan_sale_type=gigalan_sale_type,
                gigalan_old_mrc=gigalan_old_mrc,
                # Chain of Custody (CLAUDE.md §5)
                file_sha256=tx_data.get("file_sha256"),
                # Master variables snapshot: frozen at creation
                master_variables_snapshot=tx_data.get("master_variables_snapshot"),
                approval_status=ApprovalStatus.PENDING,
                # Cache: store calculated metrics at creation for zero-CPU reads
                financial_cache=clean_metrics if clean_metrics else None,
            )

            # Persist the transaction via repository
            created_tx: Transaction = self._tx_repo.create(new_transaction)

            # Build fixed cost models
            fixed_cost_models: list[FixedCost] = []
            for cost_item in data.get("fixed_costs", []):
                new_cost = FixedCost(
                    transaction_id=created_tx.id,
                    categoria=cost_item.get("categoria"),
                    tipo_servicio=cost_item.get("tipo_servicio"),
                    ticket=cost_item.get("ticket"),
                    ubicacion=cost_item.get("ubicacion"),
                    cantidad=cost_item.get("cantidad"),
                    costo_unitario_original=cost_item.get("costo_unitario_original"),
                    costo_unitario_currency=cost_item.get("costo_unitario_currency", "USD"),
                    costo_unitario_pen=cost_item.get("costo_unitario_pen"),
                    periodo_inicio=cost_item.get("periodo_inicio", 0),
                    duracion_meses=cost_item.get("duracion_meses", 1),
                )
                fixed_cost_models.append(new_cost)

            # Build recurring service models (with PEN conversion)
            save_converter = CurrencyConverter(tx_data.get("tipo_cambio", 1))
            recurring_service_models: list[RecurringService] = []

            for service_item in data.get("recurring_services", []):
                self._enrich_recurring_service_pen_fields(service_item, save_converter)

                new_service = RecurringService(
                    transaction_id=created_tx.id,
                    tipo_servicio=service_item.get("tipo_servicio"),
                    nota=service_item.get("nota"),
                    ubicacion=service_item.get("ubicacion"),
                    quantity=service_item.get("quantity"),
                    price_original=service_item.get("price_original"),
                    price_currency=service_item.get("price_currency", "PEN"),
                    price_pen=service_item.get("price_pen"),
                    cost_unit_1_original=service_item.get("cost_unit_1_original"),
                    cost_unit_2_original=service_item.get("cost_unit_2_original"),
                    cost_unit_currency=service_item.get("cost_unit_currency", "USD"),
                    cost_unit_1_pen=service_item.get("cost_unit_1_pen"),
                    cost_unit_2_pen=service_item.get("cost_unit_2_pen"),
                    proveedor=service_item.get("proveedor"),
                )
                recurring_service_models.append(new_service)

            # --- Atomic detail insertion (CLAUDE.md Section 6) ---
            # Header + detail rows must be inserted atomically.  If detail
            # creation fails, delete the orphaned header via compensating rollback.
            try:
                if fixed_cost_models:
                    self._fc_repo.create_batch(created_tx.id, fixed_cost_models)
                if recurring_service_models:
                    self._rs_repo.create_batch(created_tx.id, recurring_service_models)
            except Exception as detail_exc:
                self._logger.error(
                    "Detail row creation failed for transaction %s; "
                    "rolling back header to prevent orphaned data: %s",
                    created_tx.id,
                    detail_exc,
                )
                try:
                    self._tx_repo.supabase.table("transactions").delete().eq(
                        "id", created_tx.id
                    ).execute()
                except Exception as rollback_exc:
                    self._logger.error(
                        "Supabase header rollback also failed for %s: %s",
                        created_tx.id,
                        rollback_exc,
                    )
                # Best-effort cleanup of any partially-created detail rows
                try:
                    self._fc_repo.supabase.table("fixed_costs").delete().eq(
                        "transaction_id", created_tx.id
                    ).execute()
                    self._rs_repo.supabase.table("recurring_services").delete().eq(
                        "transaction_id", created_tx.id
                    ).execute()
                except Exception as cleanup_error:
                    self._logger.error(
                        "FK CASCADE cleanup failed for transaction %s: %s. "
                        "Database may contain orphaned detail rows.",
                        created_tx.id,
                        cleanup_error,
                        exc_info=True,
                    )
                raise detail_exc

            new_id: str = created_tx.id
            self._logger.info(
                "Transaction created with ID: %s by user %s",
                new_id,
                current_user.full_name,
            )

            # Audit trail (dual: log + SQLite)
            log_audit_event(
                logger=self._logger,
                action="CREATE",
                entity_type="Transaction",
                entity_id=new_id,
                user_id=current_user.id,
                details={
                    "client_name": tx_data.get("client_name"),
                    "unidad_negocio": tx_data.get("unidad_negocio"),
                    "salesman": current_user.full_name,
                },
                conn=self._tx_repo.sqlite,
            )

            # Send submission email (non-blocking: log error but do not fail)
            try:
                self._email_service.send_new_transaction_email(
                    salesman_name=current_user.full_name,
                    client_name=tx_data.get("client_name", "N/A"),
                    salesman_email=current_user.email,
                )
            except Exception as email_err:
                self._logger.error(
                    "Transaction saved, but email notification failed: %s",
                    str(email_err),
                )

            return ServiceResult(
                success=True,
                data={
                    "message": "Transaction saved successfully.",
                    "transaction_id": new_id,
                },
            )

        except Exception as exc:
            self._logger.error(
                "Error during save: %s\n%s",
                str(exc),
                traceback.format_exc(),
            )
            return ServiceResult(
                success=False,
                error=f"Database error: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: get_transactions (paginated list)
    # ------------------------------------------------------------------

    def get_transactions(
        self,
        current_user: User,
        page: int = 1,
        per_page: int = 30,
        search: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> ServiceResult:
        """
        Retrieves a paginated list of transactions, filtered by user role.

        - SALES: Only sees transactions where salesman matches current user.
        - FINANCE/ADMIN: Sees all transactions.

        Args:
            current_user: The authenticated user requesting the list.
            page: Page number (1-indexed).
            per_page: Number of items per page.
            search: Optional ILIKE filter on client_name or salesman.
            start_date: Optional start date filter on submission_date.
            end_date: Optional end date filter on submission_date.

        Returns:
            ServiceResult with paginated transaction data.
        """
        try:
            # RBAC: SALES users only see their own transactions
            salesman_filter: Optional[str] = None
            if current_user.role == UserRole.SALES:
                salesman_filter = current_user.full_name

            result: PaginatedTransactions = self._tx_repo.get_paginated(
                page=page,
                per_page=per_page,
                salesman_filter=salesman_filter,
                search=search,
                start_date=start_date,
                end_date=end_date,
            )

            # Column projection: exclude heavy fields from list response
            transactions_list: list[dict[str, object]] = [
                tx.model_dump(exclude={"master_variables_snapshot"})
                for tx in result["items"]
            ]

            return ServiceResult(
                success=True,
                data={
                    "transactions": transactions_list,
                    "total": result["total"],
                    "pages": result["pages"],
                    "current_page": result["current_page"],
                    "user_role": current_user.role,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Error retrieving transactions: %s", str(exc), exc_info=True
            )
            return ServiceResult(
                success=False,
                error=f"An unexpected error occurred: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: get_transaction_detail
    # ------------------------------------------------------------------

    def get_transaction_detail(
        self,
        transaction_id: str,
        current_user: User,
    ) -> ServiceResult:
        """
        Retrieves a single transaction with full details by its string ID.

        Access control: SALES can only view their own transactions.
        Includes live financial calculation for the 'timeline' (Flujo) object.
        Uses cached metrics when available to avoid expensive recalculation.

        Args:
            transaction_id: The unique transaction identifier.
            current_user: The authenticated user requesting the detail.

        Returns:
            ServiceResult with full transaction data, fixed costs,
            and recurring services.
        """
        try:
            transaction: Optional[Transaction] = self._tx_repo.get_by_id(transaction_id)

            if not transaction:
                return ServiceResult(
                    success=False,
                    error="Transaction not found or access denied.",
                    status_code=404,
                )

            # RBAC: SALES users can only load their own transactions
            if current_user.role == UserRole.SALES and transaction.salesman != current_user.full_name:
                return ServiceResult(
                    success=False,
                    error="Transaction not found or access denied.",
                    status_code=403,
                )

            # Hydrate relationships from repositories
            fixed_costs: list[FixedCost] = self._fc_repo.get_by_transaction(transaction_id)
            recurring_services: list[RecurringService] = self._rs_repo.get_by_transaction(transaction_id)
            transaction.fixed_costs = fixed_costs
            transaction.recurring_services = recurring_services

            # --- PERFORMANCE OPTIMIZATION: Use cache for immutable transactions ---
            if transaction.financial_cache:
                # Cache hit -- use stored metrics (zero CPU cost)
                clean_financial_metrics: dict[str, object] = (
                    transaction.financial_cache.model_dump()
                    if hasattr(transaction.financial_cache, "model_dump")
                    else transaction.financial_cache
                )
                transaction_details: dict[str, object] = transaction.model_dump()
                transaction_details.update(clean_financial_metrics)
            else:
                # Cache miss (legacy data or failed cache write) -- recalculate and self-heal
                self._logger.info(
                    "Cache miss for %s transaction %s - self-healing",
                    transaction.approval_status,
                    transaction.id,
                )

                # 1. Calculate and cache the metrics
                financial_metrics: dict[str, object] = calculate_financial_metrics(
                    transaction.to_financial_engine_dict(),
                ).model_dump()
                clean_financial_metrics = convert_to_json_safe(financial_metrics)

                # 3. Self-heal: Update the cache for future requests
                transaction.financial_cache = clean_financial_metrics
                self._tx_repo.update(transaction)

                # 4. Merge into transaction details
                transaction_details = transaction.model_dump()
                transaction_details.update(clean_financial_metrics)

            # --- FIX: Recalculate _pen fields if missing (for legacy data) ---
            recurring_services_list: list[dict[str, object]] = [
                rs.model_dump() for rs in transaction.recurring_services
            ]
            converter = CurrencyConverter(transaction.tipo_cambio or 1)

            for service in recurring_services_list:
                # If _pen fields are missing/zero but original values exist, recalculate
                if (
                    service.get("ingreso_pen") in [0, Decimal("0"), None]
                    and service.get("price_original")
                    and service.get("quantity")
                ):
                    price_pen: Decimal = converter.to_pen(
                        service["price_original"],
                        service.get("price_currency", "PEN"),
                    )
                    service["price_pen"] = price_pen
                    service["ingreso_pen"] = price_pen * service["quantity"]

                if service.get("egreso_pen") in [0, Decimal("0"), None] and service.get("quantity"):
                    cost_unit_1_pen: Decimal = converter.to_pen(
                        service.get("cost_unit_1_original", Decimal("0")),
                        service.get("cost_unit_currency", "USD"),
                    )
                    cost_unit_2_pen: Decimal = converter.to_pen(
                        service.get("cost_unit_2_original", Decimal("0")),
                        service.get("cost_unit_currency", "USD"),
                    )
                    service["cost_unit_1_pen"] = cost_unit_1_pen
                    service["cost_unit_2_pen"] = cost_unit_2_pen
                    service["egreso_pen"] = (cost_unit_1_pen + cost_unit_2_pen) * service["quantity"]

            return ServiceResult(
                success=True,
                data={
                    "transactions": transaction_details,
                    "fixed_costs": [fc.model_dump() for fc in transaction.fixed_costs],
                    "recurring_services": recurring_services_list,
                },
            )
        except Exception as exc:
            self._logger.error(
                "Error retrieving transaction detail for %s: %s",
                transaction_id,
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"An unexpected error occurred: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: update_transaction_content
    # ------------------------------------------------------------------

    def update_transaction_content(
        self,
        transaction_id: str,
        data_payload: dict[str, object],
        current_user: User,
    ) -> ServiceResult:
        """
        Updates a PENDING transaction's content without changing its status or ID.
        This is the dedicated service for the "Edit" feature.

        Args:
            transaction_id: The ID of the transaction to update.
            data_payload: Dictionary containing updated data with structure:
                {'transactions': {...}, 'fixed_costs': [...], 'recurring_services': [...]}
            current_user: The authenticated user performing the update.

        Returns:
            ServiceResult with updated transaction details on success.
        """
        try:
            # 1. Retrieve the transaction
            transaction: Optional[Transaction] = self._tx_repo.get_by_id(transaction_id)
            if not transaction:
                return ServiceResult(
                    success=False,
                    error="Transaction not found.",
                    status_code=404,
                )

            # 2. Validate transaction is PENDING
            if transaction.approval_status != ApprovalStatus.PENDING:
                return ServiceResult(
                    success=False,
                    error=(
                        f"Cannot edit transaction. Only 'PENDING' transactions can be edited. "
                        f"Current status: '{transaction.approval_status}'."
                    ),
                    status_code=403,
                )

            # 3. Access control: SALES can only edit their own transactions
            if current_user.role == UserRole.SALES and transaction.salesman != current_user.full_name:
                return ServiceResult(
                    success=False,
                    error="You do not have permission to edit this transaction.",
                    status_code=403,
                )

            # 4. Apply updates using the central helper
            update_result: ServiceResult = self.update_transaction_data(transaction, data_payload)
            if not update_result.success:
                return update_result

            # 5. Persist the changes via repository
            self._tx_repo.update(transaction)

            # 6. Audit trail (dual: log + SQLite)
            log_audit_event(
                logger=self._logger,
                action="UPDATE_CONTENT",
                entity_type="Transaction",
                entity_id=transaction_id,
                user_id=current_user.id,
                details={"updated_by": current_user.full_name},
                conn=self._tx_repo.sqlite,
            )

            # 7. Return the updated transaction details
            return self.get_transaction_detail(transaction_id, current_user)

        except Exception as exc:
            self._logger.error(
                "Error updating transaction content for ID %s: %s",
                transaction_id,
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"Error updating transaction: {str(exc)}",
                status_code=500,
            )

    # ------------------------------------------------------------------
    # Public: get_transaction_template
    # ------------------------------------------------------------------

    def get_transaction_template(
        self,
        current_user: User,
    ) -> ServiceResult:
        """
        Returns an empty transaction template pre-filled with current MasterVariables.

        Allows SALES users to create new transactions with the current system rates
        (tipoCambio, costoCapitalAnual, tasaCartaFianza) without requiring an Excel upload.

        Args:
            current_user: The authenticated user requesting the template.

        Returns:
            ServiceResult with template data, or error if MasterVariables missing.
        """
        try:
            # 1. Fetch current MasterVariables
            required_vars: list[str] = ["tipoCambio", "costoCapital", "tasaCartaFianza"]
            master_vars: dict[str, Optional[Decimal]] = self._variable_service.get_latest_master_variables(
                required_vars
            )

            # 2. Validate all required variables exist
            missing_vars: list[str] = [
                var for var in required_vars if master_vars.get(var) is None
            ]
            if missing_vars:
                return ServiceResult(
                    success=False,
                    error=f"System rates ({', '.join(missing_vars)}) are not configured. Please contact Finance.",
                    status_code=400,
                )

            # 3. Normalize keys at the boundary (Section 4D)
            master_vars = normalize_keys(master_vars)

            # 4. Build the default transaction template
            default_plazo: int = 36  # Default contract term in months

            template_transaction: dict[str, object] = {
                "id": None,
                "unidad_negocio": "",
                "client_name": "",
                "company_id": "",
                "salesman": current_user.full_name,
                "order_id": "",
                "tipo_cambio": master_vars["tipo_cambio"],
                "mrc_original": 0,
                "mrc_currency": "PEN",
                "mrc_pen": 0,
                "nrc_original": 0,
                "nrc_currency": "PEN",
                "nrc_pen": 0,
                "van": 0,
                "tir": 0,
                "payback": 0,
                "total_revenue": 0,
                "total_expense": 0,
                "comisiones": 0,
                "comisiones_rate": 0,
                "costo_instalacion": 0,
                "costo_instalacion_ratio": 0,
                "gross_margin": 0,
                "gross_margin_ratio": 0,
                "plazo_contrato": default_plazo,
                "costo_capital_anual": master_vars["costo_capital"],
                "tasa_carta_fianza": master_vars["tasa_carta_fianza"],
                "costo_carta_fianza": 0,
                "aplica_carta_fianza": True,
                "gigalan_region": None,
                "gigalan_sale_type": None,
                "gigalan_old_mrc": None,
                "approval_status": ApprovalStatus.PENDING,
                "submission_date": None,
                "approval_date": None,
                "rejection_note": None,
                # Include empty timeline for frontend compatibility
                "timeline": initialize_timeline(default_plazo),
            }

            return ServiceResult(
                success=True,
                data={
                    "transactions": template_transaction,
                    "fixed_costs": [],
                    "recurring_services": [],
                },
            )

        except Exception as exc:
            self._logger.error(
                "Error generating transaction template: %s",
                str(exc),
                exc_info=True,
            )
            return ServiceResult(
                success=False,
                error=f"An unexpected error occurred: {str(exc)}",
                status_code=500,
            )
