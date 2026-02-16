"""
Pydantic Models for Phase 3 Card Engine.

Data transfer object bridging raw ``InboxFile`` filesystem metadata
with parsed Excel header fields.  This is the sole typed boundary
between the ``InboxScanService`` and the Card Engine UI.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.models.enums import BusinessUnit, FileStatus


class CardData(BaseModel):
    """Enriched file card data for the Card Engine UI.

    Combines filesystem metadata (from ``InboxFile``) with parsed
    Excel header fields (from ``ExcelParserService.extract_metadata``).

    Attributes
    ----------
    path:
        Absolute path to the ``.xlsx`` file in the inbox.
    filename:
        File name extracted from *path* for display.
    size_bytes:
        File size in bytes at scan time.
    modified_at:
        Last-modified timestamp from the filesystem.
    file_status:
        Readiness result from ``FileGuardsService``.
    sha256:
        SHA-256 hex digest (chain of custody).  ``None`` if the
        file was not in READY state when scanned.
    client_name:
        Extracted from Excel cell C2.
    salesman:
        Extracted from Excel cell C3.
    business_unit:
        Extracted from Excel cell C4.
    company_id:
        Extracted from Excel cell C5.
    order_id:
        Extracted from Excel cell C6.
    mrc:
        Monthly Recurring Charge — raw value from Excel cell C7.
    nrc:
        Non-Recurring Charge — raw value from Excel cell C8.
    plazo_contrato:
        Contract term in months from Excel cell C9.
    parse_error:
        Human-readable error message if metadata extraction failed.
    is_parsed:
        ``True`` when header metadata was successfully extracted.
    """

    model_config = {"arbitrary_types_allowed": True}

    # Filesystem fields (from InboxFile)
    path: Path
    filename: str
    size_bytes: int
    modified_at: datetime
    file_status: FileStatus

    # Chain of custody (from FileGuardsService)
    sha256: Optional[str] = None

    # Parsed Excel metadata (from ExcelParserService.extract_metadata)
    client_name: Optional[str] = None
    salesman: Optional[str] = None
    business_unit: Optional[BusinessUnit] = None
    company_id: Optional[int] = None
    order_id: Optional[int] = None
    mrc: Optional[Decimal] = None
    nrc: Optional[Decimal] = None
    plazo_contrato: Optional[int] = None

    # Parse state
    parse_error: Optional[str] = None
    is_parsed: bool = False
