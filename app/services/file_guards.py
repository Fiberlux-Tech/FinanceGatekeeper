"""
File Safety Guards Service.

Provides three safety mechanisms required before any file can be
ingested or approved:

1. **File Lock Detection** — detects if Excel (or another process)
   holds the file open.
2. **Steady State Check** — waits until OneDrive / Power Automate
   finishes syncing the file (size stabilisation + temp-file check).
3. **SHA-256 Hashing** — computes the chain-of-custody fingerprint.

All blocking operations (``is_file_stable``) use ``time.sleep`` and
**must** be called from a worker thread, never the main UI thread.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from app.config import AppConfig
from app.logger import StructuredLogger
from app.models.enums import FileStatus
from app.models.file_models import FileCheckResult
from app.services.base_service import BaseService

_HASH_CHUNK_SIZE: int = 65_536  # 64 KB read chunks for SHA-256


class FileGuardsService(BaseService):
    """Safety guards for file readiness and integrity.

    Parameters
    ----------
    config:
        Application configuration (steady-state timing parameters).
    logger:
        Structured logger instance.
    """

    def __init__(self, config: AppConfig, logger: StructuredLogger) -> None:
        super().__init__(logger)
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_file_status(self, path: Path) -> FileCheckResult:
        """Run all safety checks on *path* and return an aggregate result.

        Check order: temp marker → file lock → size stability.

        Returns
        -------
        FileCheckResult
            ``READY`` if the file is safe to ingest, otherwise
            ``LOCKED`` or ``SYNCING`` with a human-readable message.
        """
        if not path.exists():
            return FileCheckResult(
                status=FileStatus.SYNCING,
                message="File not found — it may still be syncing.",
            )

        if self.has_temp_marker(path):
            return FileCheckResult(
                status=FileStatus.SYNCING,
                message=(
                    "An Excel temporary file was detected. "
                    "The file is still being written or is open in Excel."
                ),
            )

        if self.is_file_locked(path):
            return FileCheckResult(
                status=FileStatus.LOCKED,
                message=(
                    "Please close the Excel file before finalizing approval."
                ),
            )

        if not self.is_file_stable(path):
            return FileCheckResult(
                status=FileStatus.SYNCING,
                message=(
                    "File size is still changing — OneDrive or Power "
                    "Automate may be syncing. Please wait a moment."
                ),
            )

        return FileCheckResult(
            status=FileStatus.READY,
            message="File is stable and ready for processing.",
        )

    def is_file_locked(self, path: Path) -> bool:
        """Return ``True`` if *path* is held open by another process.

        On Windows, attempts to open the file with exclusive read-write
        access.  A ``PermissionError`` indicates an active OS file lock.
        """
        try:
            with open(path, "rb+"):
                return False
        except PermissionError:
            return True
        except OSError:
            return True

    def is_file_stable(self, path: Path) -> bool:
        """Return ``True`` if the file size is constant over the check window.

        Takes ``STEADY_STATE_CHECKS`` size readings separated by
        ``STEADY_STATE_WAIT_S / STEADY_STATE_CHECKS`` seconds.

        .. warning:: This method calls ``time.sleep`` and **must** be
           invoked from a background thread.
        """
        checks: int = max(self._config.STEADY_STATE_CHECKS, 2)
        interval: float = self._config.STEADY_STATE_WAIT_S / checks

        try:
            previous_size: int = path.stat().st_size
        except OSError:
            return False

        for _ in range(checks - 1):
            time.sleep(interval)
            try:
                current_size: int = path.stat().st_size
            except OSError:
                return False
            if current_size != previous_size:
                return False
            previous_size = current_size

        return True

    def has_temp_marker(self, path: Path) -> bool:
        """Return ``True`` if an Excel temporary file exists for *path*.

        Excel creates ``~$filename.xlsx`` while a workbook is open.
        """
        temp_name = f"~${path.name}"
        return (path.parent / temp_name).exists()

    def compute_sha256(self, path: Path) -> str:
        """Compute the SHA-256 hex digest of *path*.

        Reads the file in 64 KB chunks to keep memory usage constant
        regardless of file size.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        PermissionError
            If the file is locked by another process.
        """
        sha = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk: bytes = fh.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)

        digest: str = sha.hexdigest()
        self._logger.debug("SHA-256 for %s: %s", path.name, digest)
        return digest
