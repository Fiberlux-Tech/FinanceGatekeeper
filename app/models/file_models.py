"""
Pydantic Models for Phase 2 File Observation.

Data transfer objects for path discovery, file watching, and safety guards.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.models.enums import BusinessUnit, FileEventType, FileStatus


class ResolvedPaths(BaseModel):
    """Result of SharePoint/OneDrive path discovery."""

    model_config = {"arbitrary_types_allowed": True}

    sharepoint_root: Path
    inbox: Path
    archive_approved: Path
    archive_rejected: Path


class InboxFile(BaseModel):
    """Snapshot of a single file detected in the inbox.

    ``business_unit`` is ``None`` at discovery time because all files
    land in a flat ``01_INBOX`` directory.  The BU is determined later
    when the Excel file is parsed (from a cell value).
    """

    model_config = {"arbitrary_types_allowed": True}

    path: Path
    filename: str
    business_unit: Optional[BusinessUnit] = None
    size_bytes: int
    modified_at: datetime
    status: FileStatus
    sha256: Optional[str] = None


class FileEvent(BaseModel):
    """Event emitted by the file watcher when an inbox file changes."""

    event_type: FileEventType
    file: InboxFile
    timestamp: datetime


class FileCheckResult(BaseModel):
    """Result of a file readiness / safety-guard check."""

    status: FileStatus
    message: str


class ArchivalResult(BaseModel):
    """Result of a file archival operation.

    Returned by ``FileArchivalService.archive_approved`` and
    ``archive_rejected`` to provide metadata about the completed
    file move.

    Attributes
    ----------
    source_path:
        Original path of the file in the inbox.
    archived_path:
        Final destination path after the move.
    sha256:
        SHA-256 hex digest verified before the move.
    transaction_id:
        The transaction this archival belongs to.
    business_unit:
        Business unit used for folder routing.
    archived_at:
        UTC timestamp when the file was archived.
    """

    model_config = {"arbitrary_types_allowed": True}

    source_path: Path
    archived_path: Path
    sha256: str
    transaction_id: str
    business_unit: BusinessUnit
    archived_at: datetime
