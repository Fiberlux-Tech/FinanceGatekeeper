# app/services/excel_parser.py
# (This file is responsible for all Excel file ingestion and parsing.)

"""
Excel Parser Service.

Refactored from legacy Flask-coupled function to a dependency-injected service class.

Stripped:
    - from flask import current_app
    - @require_jwt decorator
    - Inner helper functions (now module-level with type hints)
    - print() statements (replaced with structured logging)

Injected:
    - VariableService (for master variable lookups)
    - logging.Logger (via BaseService)
    - AppConfig (via get_config())

Chain of Custody:
    - SHA-256 hash computed at file ingestion boundary (CLAUDE.md mandate).

Defensive File Handling:
    - PermissionError caught and surfaced as user-friendly ServiceResult.
"""

from __future__ import annotations

import hashlib
import traceback
from datetime import datetime, timezone
from typing import BinaryIO, Optional, Union

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.config import AppConfig, get_config
from app.logger import StructuredLogger
from app.models.service_models import ServiceResult
from app.services.base_service import BaseService
from app.services.financial_engine import calculate_financial_metrics
from app.services.variables import VariableService
from app.utils.audit import log_audit_event
from app.utils.general import convert_to_json_safe
from app.utils.string_helpers import normalize_keys


# ---------------------------------------------------------------------------
# Module-level type-safe conversion helpers
# ---------------------------------------------------------------------------

def safe_float(val: Union[int, float, str, None], logger: StructuredLogger) -> float:
    """Convert a value to float, treating None, empty strings, and invalid values as 0.0.

    When using ``data_only=True``, openpyxl may return Excel error strings
    (#VALUE!, #DIV/0!, etc.) instead of computed values.  These are detected
    and logged so broken templates can be identified.

    Args:
        val: The raw cell value from openpyxl.
        logger: Logger instance for warning on conversion failures.

    Returns:
        The float representation of *val*, or ``0.0`` on failure.
    """
    if val is not None and val != '':
        # Check for Excel error strings
        if isinstance(val, str) and val.startswith('#'):
            logger.warning("Excel error detected in cell: %s - Template may be broken", val)
            return 0.0

        try:
            return float(val)
        except (ValueError, TypeError):
            logger.warning(
                "Failed to convert value to float: %s (type: %s)", val, type(val).__name__,
            )
            return 0.0
    return 0.0


def safe_int(val: Union[int, float, str, None]) -> int:
    """Convert a value to int, treating None, empty strings, and invalid values as 0.

    Handles string representations of floats (e.g. ``"5.0"`` -> ``5``) which
    are common in Excel exports.

    Args:
        val: The raw cell value from openpyxl.

    Returns:
        The integer representation of *val*, or ``0`` on failure.
    """
    if val is not None and val != '':
        try:
            return int(float(val))  # Handle "5.0" string -> 5
        except (ValueError, TypeError):
            return 0
    return 0


def _compute_sha256(file_stream: BinaryIO) -> str:
    """Compute the SHA-256 hash of a file stream.

    The stream position is reset to the beginning after hashing so
    subsequent reads start from byte zero.

    Args:
        file_stream: A seekable binary file-like object.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    file_stream.seek(0)
    sha256_hash = hashlib.sha256()
    while True:
        chunk: bytes = file_stream.read(8192)
        if not chunk:
            break
        sha256_hash.update(chunk)
    file_stream.seek(0)
    return sha256_hash.hexdigest()


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class ExcelParserService(BaseService):
    """Service for ingesting and parsing Excel financial templates.

    Orchestrates reading, validating, and calculating data from the uploaded
    Excel file, using master variables for key financial rates.

    Dependencies are injected via ``__init__`` -- no Flask globals are used.
    """

    def __init__(
        self,
        variable_service: VariableService,
        logger: StructuredLogger,
    ) -> None:
        """Initialise the parser with its runtime dependencies.

        Args:
            variable_service: Service used to retrieve master variable rates.
            logger: Logger instance for structured logging.
        """
        super().__init__(logger)
        self._variable_service: VariableService = variable_service
        self._config: AppConfig = get_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_excel_file(self, excel_file: BinaryIO) -> ServiceResult:
        """Parse the uploaded Excel template and return calculated financial data.

        High-level flow:
            1. Compute SHA-256 hash at the ingestion boundary (chain of custody).
            2. Fetch and validate master variable rates.
            3. Open the workbook and extract header, recurring services, and
               fixed costs data.
            4. Normalize keys, inject master variables, validate required fields.
            5. Delegate to the financial engine for metric calculation.
            6. Assemble and return the final data package.

        Args:
            excel_file: A seekable binary file-like object containing the
                Excel workbook (.xlsx).

        Returns:
            A ``ServiceResult`` with ``success=True`` and the parsed data
            package on success, or ``success=False`` with an error description
            on failure.
        """
        try:
            # --- CHAIN OF CUSTODY: SHA-256 at the ingestion boundary ---
            file_hash: str = _compute_sha256(excel_file)
            self._logger.info("File ingested. SHA-256: %s", file_hash)

            log_audit_event(
                logger=self._logger,
                action="INGEST_EXCEL",
                entity_type="ExcelFile",
                entity_id=file_hash,
                user_id="system",
                details={"sha256": file_hash, "timestamp": datetime.now(timezone.utc).isoformat()},
            )

            # --- FETCH LATEST MASTER VARIABLES (Decoupling) ---
            required_master_variables: list[str] = ['tipoCambio', 'costoCapital', 'tasaCartaFianza']
            latest_rates: dict[str, Optional[float]] = (
                self._variable_service.get_latest_master_variables(required_master_variables)
            )
            latest_rates = normalize_keys(latest_rates)

            # Check if the necessary rates were found in the DB (CRITICAL VALIDATION)
            if (
                latest_rates.get('tipo_cambio') is None
                or latest_rates.get('costo_capital') is None
                or latest_rates.get('tasa_carta_fianza') is None
            ):
                return ServiceResult(
                    success=False,
                    error=(
                        "Cannot calculate financial metrics. System rates (Tipo de Cambio, "
                        "Costo Capital, or Tasa Carta Fianza) are missing. Please ensure they "
                        "have been set by the Finance department."
                    ),
                    status_code=400,
                )

            # --- MASTER VARIABLES SNAPSHOT: Freeze rates at upload time ---
            master_variables_snapshot: dict[str, Union[float, str, None]] = {
                'tipo_cambio': latest_rates['tipo_cambio'],
                'costo_capital': latest_rates['costo_capital'],
                'tasa_carta_fianza': latest_rates['tasa_carta_fianza'],
                'captured_at': datetime.now(timezone.utc).isoformat(),
            }

            # --- OPEN AND READ WORKBOOK ---
            self._logger.info("Reading Excel file with openpyxl (read_only mode for memory optimization)")
            excel_file.seek(0)

            workbook: Optional[Workbook] = None
            try:
                workbook = load_workbook(excel_file, read_only=True, data_only=True)
                worksheet: Worksheet = workbook[self._config.PLANTILLA_SHEET_NAME]  # 'PLANTILLA'

                self._logger.info(
                    "Excel sheet loaded: %s rows x %s columns",
                    worksheet.max_row,
                    worksheet.max_column,
                )

                # Step 3: Read & Extract Header Data using direct cell access
                header_data: dict[str, Union[float, str]] = {}
                for var_name, cell_ref in self._config.VARIABLES_TO_EXTRACT.items():
                    # Use coordinate string directly -- much cleaner than manual parsing!
                    # Example: worksheet['C2'].value directly accesses cell C2
                    cell_value = worksheet[cell_ref].value

                    # Convert based on expected data type
                    if var_name in ['MRC', 'NRC', 'plazoContrato', 'comisiones', 'companyID', 'orderID']:
                        header_data[var_name] = safe_float(cell_value, self._logger)
                    else:
                        header_data[var_name] = str(cell_value) if cell_value is not None else ""

                # Normalize all header_data keys to snake_case at the ingestion boundary
                header_data = normalize_keys(header_data)

                # This logic is now OVERWRITTEN by the refactor. The real commission is calculated later.
                if 'comisiones' in header_data:
                    header_data['comisiones'] = 0.0

                # --- INJECT MASTER VARIABLES INTO HEADER DATA ---
                header_data['tipo_cambio'] = latest_rates['tipo_cambio']
                header_data['costo_capital_anual'] = latest_rates['costo_capital']
                header_data['tasa_carta_fianza'] = latest_rates['tasa_carta_fianza']
                header_data['aplica_carta_fianza'] = False  # Default to NO
                header_data['master_variables_snapshot'] = master_variables_snapshot  # Frozen audit trail
                # --- END INJECTION ---

                # Extract recurring services with manual iteration (openpyxl)
                recurring_services_data: list[dict[str, Union[int, float, str, None]]] = []
                services_start_row: int = self._config.RECURRING_SERVICES_START_ROW
                services_columns: dict[str, str] = self._config.RECURRING_SERVICES_COLUMNS

                empty_row_count: int = 0
                MAX_EMPTY_ROWS: int = 5  # Stop after 5 consecutive empty rows

                # Iterate from start row to max_row
                for row_idx in range(services_start_row + 1, worksheet.max_row + 1):  # +1 for 1-based indexing
                    row_data: dict[str, Union[int, float, str, None]] = {}
                    is_empty_row: bool = True

                    # Extract each column value
                    for field_name, col_letter in services_columns.items():
                        col_idx: int = column_index_from_string(col_letter)
                        cell_value = worksheet.cell(row=row_idx, column=col_idx).value

                        # Track if row has any non-empty cells
                        # IMPORTANT: Strip whitespace -- a cell with " " should be treated as empty
                        if cell_value is not None and str(cell_value).strip() != '':
                            is_empty_row = False

                        row_data[field_name] = cell_value

                    # Skip completely empty rows (equivalent to dropna(how='all'))
                    if is_empty_row:
                        empty_row_count += 1
                        if empty_row_count >= MAX_EMPTY_ROWS:
                            break  # Stop reading after 5 consecutive empty rows
                    else:
                        empty_row_count = 0  # Reset counter
                        # Normalize raw Excel column names to snake_case at the boundary
                        row_data = normalize_keys(row_data)
                        recurring_services_data.append(row_data)

                self._logger.info("SUCCESS: Read %d recurring service records", len(recurring_services_data))

                # Extract fixed costs with manual iteration (openpyxl)
                fixed_costs_data: list[dict[str, Union[int, float, str, None]]] = []
                fixed_costs_start_row: int = self._config.FIXED_COSTS_START_ROW
                fixed_costs_columns: dict[str, str] = self._config.FIXED_COSTS_COLUMNS

                self._logger.debug("Fixed Costs Extraction - Starting from row: %d, Expected columns: %d", fixed_costs_start_row + 1, len(fixed_costs_columns))

                empty_row_count = 0
                MAX_EMPTY_ROWS = 5  # Stop after 5 consecutive empty rows

                # Iterate from start row to max_row
                for row_idx in range(fixed_costs_start_row + 1, worksheet.max_row + 1):
                    row_data = {}
                    is_empty_row = True

                    # Extract each column value
                    for field_name, col_letter in fixed_costs_columns.items():
                        col_idx = column_index_from_string(col_letter)
                        cell_value = worksheet.cell(row=row_idx, column=col_idx).value

                        # Track if row has any non-empty cells
                        # IMPORTANT: Strip whitespace -- a cell with " " should be treated as empty
                        if cell_value is not None and str(cell_value).strip() != '':
                            is_empty_row = False

                        row_data[field_name] = cell_value

                    # Skip completely empty rows
                    if is_empty_row:
                        empty_row_count += 1
                        if empty_row_count >= MAX_EMPTY_ROWS:
                            break  # Stop reading after 5 consecutive empty rows
                    else:
                        empty_row_count = 0  # Reset counter
                        # Normalize raw Excel column names to snake_case at the boundary
                        row_data = normalize_keys(row_data)
                        fixed_costs_data.append(row_data)

                self._logger.debug("Read %d fixed cost records", len(fixed_costs_data))

                # Calculate totals for preview
                for item in fixed_costs_data:
                    # Rename to _original pattern
                    item['costo_unitario_original'] = safe_float(item.get('costo_unitario', 0), self._logger)
                    item['costo_unitario_currency'] = 'USD'

                    # Calculate total for preview (in original currency)
                    cantidad = item.get('cantidad')
                    costo_original = item.get('costo_unitario_original')
                    if cantidad is not None and costo_original is not None:
                        item['total'] = cantidad * costo_original

                    item['periodo_inicio'] = safe_float(item.get('periodo_inicio', 0), self._logger)
                    item['duracion_meses'] = safe_float(item.get('duracion_meses', 1), self._logger)

                for item in recurring_services_data:
                    q: float = safe_float(item.get('q', 0), self._logger)
                    p_original: float = safe_float(item.get('p', 0), self._logger)
                    cu1_original: float = safe_float(item.get('cu1', 0), self._logger)
                    cu2_original: float = safe_float(item.get('cu2', 0), self._logger)

                    # Rename to snake_case _original pattern
                    item['quantity'] = q
                    item['price_original'] = p_original
                    item['price_currency'] = 'PEN'
                    item['cost_unit_1_original'] = cu1_original
                    item['cost_unit_2_original'] = cu2_original
                    item['cost_unit_currency'] = 'USD'

                    # Calculate preview values in original currency
                    item['ingreso'] = q * p_original
                    item['egreso'] = (cu1_original + cu2_original) * q

                # <-- MODIFIED: This is the total in *original* currency, not PEN
                calculated_costo_instalacion: float = sum(
                    item.get('total', 0) for item in fixed_costs_data if item.get('total') is not None
                )

                # Step 4: Validate Inputs
                client_name: Union[str, None] = header_data.get('client_name')
                mrc_value: Union[float, None] = header_data.get('mrc')
                if not client_name or client_name == '' or mrc_value is None or mrc_value == '':
                    return ServiceResult(
                        success=False,
                        error="Required field 'Client Name' or 'MRC' is missing from the Excel file.",
                        status_code=400,
                    )

                # Rename to _original pattern for transaction
                header_data['mrc_original'] = header_data.get('mrc')
                header_data['mrc_currency'] = 'PEN'
                header_data['nrc_original'] = header_data.get('nrc')
                header_data['nrc_currency'] = 'PEN'

                # Consolidate all extracted data
                full_extracted_data: dict[str, object] = {
                    **header_data,
                    'recurring_services': recurring_services_data,
                    'fixed_costs': fixed_costs_data,
                    'costo_instalacion': calculated_costo_instalacion,
                }

                # Step 5: Calculate Metrics
                # This function now calculates *all* metrics, including the *real* commission.
                # It needs the GIGALAN/Unidad fields, but they are not in the Excel file.
                # They will be None, so commission will correctly calculate as 0.0 for now.
                # This is the *correct* initial state.
                # <-- This function now handles all PEN conversions internally
                financial_metrics: dict[str, object] = calculate_financial_metrics(full_extracted_data)

                # Step 6: Assemble the Final Response
                # <-- MODIFIED: 'costo_instalacion' is now the PEN-based value from financial_metrics
                transaction_summary: dict[str, object] = {
                    **header_data,
                    **financial_metrics,
                    "costo_instalacion": financial_metrics.get('costo_instalacion'),  # This is now PEN
                    "submission_date": None,
                    "approval_status": "PENDING",
                }

                final_data_package: dict[str, object] = {
                    "transactions": transaction_summary,
                    "fixed_costs": fixed_costs_data,
                    "recurring_services": recurring_services_data,
                }

                clean_data: object = convert_to_json_safe(final_data_package)

                # Attach file hash to the result for downstream chain-of-custody tracking
                if isinstance(clean_data, dict):
                    clean_data["file_sha256"] = file_hash

                return ServiceResult(success=True, data=clean_data)

            finally:
                # Always close the workbook to free resources
                if workbook:
                    workbook.close()
                    self._logger.info("Workbook closed successfully")

        except PermissionError as perm_err:
            # Defensive file handling per CLAUDE.md mandate:
            # Handle OS-level file locks gracefully with user-friendly warnings.
            self._logger.error(
                "PermissionError during Excel processing: %s\n%s",
                perm_err,
                traceback.format_exc(),
            )
            return ServiceResult(
                success=False,
                error=(
                    "The Excel file could not be accessed because it is locked by another "
                    "application. Please close any programs that may have the file open "
                    "and try again."
                ),
                status_code=423,
            )

        except Exception as exc:
            self._logger.error(
                "Unexpected error during Excel processing: %s\n%s",
                exc,
                traceback.format_exc(),
            )
            return ServiceResult(
                success=False,
                error=f"An unexpected error occurred: {str(exc)}",
                status_code=500,
            )
