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
