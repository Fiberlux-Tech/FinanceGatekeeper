"""
Transaction Preview Service.

Handles stateless financial metric preview calculations.
Refactored from legacy transactions.py -- class-based, repository-backed,
with structured audit logging and ServiceResult envelope.

Functions ported:
    - calculate_preview_metrics
"""

from __future__ import annotations

import traceback
from app.models.user import User
from app.logger import StructuredLogger
from app.models.service_models import ServiceResult
from app.services.base_service import BaseService
from app.services.financial_engine import calculate_financial_metrics
from app.utils.general import convert_to_json_safe


class TransactionPreviewService(BaseService):
    """
    Service handling stateless financial metric previews.

    This is a "calculator" service -- it does not read from or write to
    any repository. All data comes from the request payload.

    Dependencies are injected via __init__ -- no global state, no Flask.
    """

    def __init__(
        self,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)

    # ------------------------------------------------------------------
    # Public: calculate_preview_metrics
    # ------------------------------------------------------------------

    def calculate_preview_metrics(
        self,
        request_data: dict[str, object],
        current_user: User,
    ) -> ServiceResult:
        """
        Calculates all financial metrics based on temporary data from the
        frontend modal. This is a "stateless" calculator.

        This method TRUSTS the 'tipoCambio' and 'costoCapitalAnual'
        sent in the 'request_data' packet. It does NOT fetch the latest
        master variables, ensuring the preview is consistent with the
        transaction's "locked-in" rates.

        Args:
            request_data: The full preview payload with keys:
                'transactions', 'fixed_costs', 'recurring_services'.
            current_user: The authenticated user requesting the preview.

        Returns:
            ServiceResult with calculated financial metrics on success.
        """
        try:
            # 1. Extract data from the request payload
            transaction_data: dict[str, object] = request_data.get("transactions", {})
            fixed_costs_data: list[dict[str, object]] = request_data.get("fixed_costs", [])
            recurring_services_data: list[dict[str, object]] = request_data.get("recurring_services", [])

            # 2. Build the complete data dictionary
            # This mimics the data package created in 'process_excel_file'
            full_data_package: dict[str, object] = {**transaction_data}

            # --- Validation: check required rates in the data packet ---
            if (
                full_data_package.get("tipo_cambio") is None
                or full_data_package.get("costo_capital_anual") is None
                or full_data_package.get("tasa_carta_fianza") is None
            ):
                return ServiceResult(
                    success=False,
                    error=(
                        "Transaction data is missing 'Tipo de Cambio', "
                        "'Costo Capital', or 'Tasa Carta Fianza'."
                    ),
                    status_code=400,
                )

            # Add the cost/service lists
            full_data_package["fixed_costs"] = fixed_costs_data
            full_data_package["recurring_services"] = recurring_services_data

            # Calculate costo_instalacion as the sum of fixed cost totals
            # (original currency total -- calculate_financial_metrics handles PEN conversion)
            full_data_package["costo_instalacion"] = sum(
                item.get("total", 0)
                for item in fixed_costs_data
                if item.get("total") is not None
            )

            # 3. Call the stateless calculator
            financial_metrics: dict[str, object] = calculate_financial_metrics(
                full_data_package,
            ).model_dump()

            # 4. Clean and return the results
            clean_metrics: dict[str, object] = convert_to_json_safe(financial_metrics)

            # 5. Merge the original transaction inputs with the newly calculated metrics
            # This ensures inputs like 'plazoContrato' are returned in the response
            final_data: dict[str, object] = {**transaction_data, **clean_metrics}

            return ServiceResult(
                success=True,
                data=final_data,
            )

        except Exception as exc:
            self._logger.error(
                "Error during preview calculation: %s\n%s",
                str(exc),
                traceback.format_exc(),
            )
            return ServiceResult(
                success=False,
                error=f"An unexpected error occurred during preview: {str(exc)}",
                status_code=500,
            )
