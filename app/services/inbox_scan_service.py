"""
Inbox Scan Service.

Orchestrates the process of scanning the inbox folder, computing
SHA-256 hashes, and extracting lightweight Excel metadata to produce
enriched ``CardData`` objects for the Card Engine UI.

This service composes three existing services:

- ``FileWatcherService`` — file listing (``get_inbox_files``)
- ``FileGuardsService`` — readiness checks + SHA-256 hashing
- ``ExcelParserService`` — header-cell metadata extraction

All public methods perform blocking I/O (steady-state polling,
file reads) and **must** be called from a worker thread.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from app.logger import StructuredLogger
from app.models.card_models import CardData
from app.models.enums import BusinessUnit, FileStatus
from app.models.file_models import FileCheckResult, InboxFile
from app.models.service_models import ServiceResult
from app.services.base_service import BaseService
from app.services.excel_parser import ExcelParserService
from app.services.file_guards import FileGuardsService
from app.services.file_watcher import FileWatcherService


class InboxScanService(BaseService):
    """Scan the inbox and produce enriched ``CardData`` for the Card Engine.

    Parameters
    ----------
    file_watcher:
        Provides the raw list of ``.xlsx`` files in the inbox.
    file_guards:
        Safety checks (lock, stability, temp marker) and SHA-256.
    excel_parser:
        Lightweight header-cell extraction (``extract_metadata``).
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        file_watcher: FileWatcherService,
        file_guards: FileGuardsService,
        excel_parser: ExcelParserService,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._file_watcher = file_watcher
        self._file_guards = file_guards
        self._excel_parser = excel_parser

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_inbox(self) -> list[CardData]:
        """Return enriched ``CardData`` for every ``.xlsx`` in the inbox.

        For each file the method:

        1. Reads filesystem metadata via ``file_watcher.get_inbox_files()``.
        2. Runs safety checks via ``file_guards.check_file_status()``.
        3. Computes SHA-256 via ``file_guards.compute_sha256()``
           (only for READY files).
        4. Extracts header metadata via ``excel_parser.extract_metadata()``
           (only for READY files).
        5. Assembles a ``CardData`` object.

        Each file is processed independently — one failure never blocks
        the remaining files.

        .. warning::
            This method calls ``file_guards.is_file_stable()`` which
            uses ``time.sleep``.  **Must** be called from a worker thread.

        Returns
        -------
        list[CardData]
            One entry per ``.xlsx`` file discovered in the inbox,
            sorted by ``modified_at`` descending (newest first).
        """
        inbox_files: list[InboxFile] = self._file_watcher.get_inbox_files()
        cards: list[CardData] = []

        for inbox_file in inbox_files:
            card = self._build_card_data(inbox_file)
            cards.append(card)

        # Newest files first
        cards.sort(key=lambda c: c.modified_at, reverse=True)

        self._logger.info(
            "Inbox scan complete: %d files found", len(cards),
        )
        return cards

    def scan_single_file(self, path: Path) -> CardData:
        """Build a ``CardData`` for a single file path.

        Used for incremental updates when the watchdog fires a
        CREATED or MODIFIED event, or when the user clicks Refresh
        on a specific card.

        If the file has disappeared, returns a degraded ``CardData``
        with ``file_status=SYNCING`` and a descriptive ``parse_error``.

        .. warning::
            Calls blocking I/O — must be invoked from a worker thread.

        Parameters
        ----------
        path:
            Absolute path to the ``.xlsx`` file.

        Returns
        -------
        CardData
            Enriched card data for the given file.
        """
        try:
            stat = path.stat()
        except OSError:
            self._logger.warning(
                "File disappeared before scan: %s", path.name,
            )
            return self._make_missing_card(path)

        inbox_file = InboxFile(
            path=path,
            filename=path.name,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            status=FileStatus.READY,
        )
        return self._build_card_data(inbox_file)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_card_data(self, inbox_file: InboxFile) -> CardData:
        """Process a single ``InboxFile`` into an enriched ``CardData``.

        Steps:
        1. Check file readiness.
        2. Compute SHA-256 (if READY).
        3. Extract header metadata (if READY).
        4. Parse business-unit string into the ``BusinessUnit`` enum.
        5. Assemble ``CardData``.
        """
        path: Path = inbox_file.path
        sha256: Optional[str] = None
        client_name: Optional[str] = None
        salesman: Optional[str] = None
        business_unit: Optional[BusinessUnit] = None
        company_id: Optional[int] = None
        order_id: Optional[int] = None
        mrc: Optional[float] = None
        nrc: Optional[float] = None
        plazo_contrato: Optional[int] = None
        parse_error: Optional[str] = None
        is_parsed: bool = False

        # Step 1: File readiness
        try:
            check: FileCheckResult = self._file_guards.check_file_status(path)
            file_status: FileStatus = check.status
        except Exception as exc:
            self._logger.warning(
                "File guard check failed for %s: %s", path.name, exc,
            )
            file_status = FileStatus.SYNCING
            parse_error = f"Could not check file status: {exc}"

        if file_status == FileStatus.READY:
            # Step 2: SHA-256
            try:
                sha256 = self._file_guards.compute_sha256(path)
            except (FileNotFoundError, PermissionError, OSError) as exc:
                self._logger.warning(
                    "SHA-256 computation failed for %s: %s", path.name, exc,
                )
                parse_error = f"Could not compute hash: {exc}"

            # Step 3: Header metadata
            result: ServiceResult = self._excel_parser.extract_metadata(path)
            if result.success and isinstance(result.data, dict):
                data: dict[str, Union[int, float, str, None]] = result.data
                client_name = _safe_str(data.get("client_name"))
                salesman = _safe_str(data.get("salesman"))
                company_id = _safe_optional_int(data.get("company_id"))
                order_id = _safe_optional_int(data.get("order_id"))
                mrc = _safe_optional_float(data.get("mrc"))
                nrc = _safe_optional_float(data.get("nrc"))
                plazo_contrato = _safe_optional_int(data.get("plazo_contrato"))
                business_unit = _parse_business_unit(
                    data.get("unidad_negocio"),
                )
                is_parsed = True
            else:
                parse_error = result.error or "Unknown parse error"
        else:
            parse_error = check.message

        return CardData(
            path=path,
            filename=inbox_file.filename,
            size_bytes=inbox_file.size_bytes,
            modified_at=inbox_file.modified_at,
            file_status=file_status,
            sha256=sha256,
            client_name=client_name,
            salesman=salesman,
            business_unit=business_unit,
            company_id=company_id,
            order_id=order_id,
            mrc=mrc,
            nrc=nrc,
            plazo_contrato=plazo_contrato,
            parse_error=parse_error,
            is_parsed=is_parsed,
        )

    def _make_missing_card(self, path: Path) -> CardData:
        """Create a degraded ``CardData`` for a file that disappeared."""
        return CardData(
            path=path,
            filename=path.name,
            size_bytes=0,
            modified_at=datetime.now(tz=timezone.utc),
            file_status=FileStatus.SYNCING,
            parse_error="File not found — it may still be syncing.",
            is_parsed=False,
        )


# ======================================================================
# Module-level helpers
# ======================================================================


def _safe_str(val: Union[float, str, None]) -> Optional[str]:
    """Coerce a value to ``str`` or return ``None``."""
    if val is None or val == "":
        return None
    return str(val)


def _safe_optional_float(val: Union[float, str, int, None]) -> Optional[float]:
    """Coerce to ``float`` or return ``None``."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_optional_int(val: Union[float, str, int, None]) -> Optional[int]:
    """Coerce to ``int`` or return ``None``."""
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_business_unit(val: Union[float, str, None]) -> Optional[BusinessUnit]:
    """Parse a raw string into a ``BusinessUnit`` enum, or ``None``."""
    if val is None or val == "":
        return None
    raw: str = str(val).strip().upper()
    try:
        return BusinessUnit(raw)
    except ValueError:
        return None
