"""
Native File Opener Service.

Opens Excel workbooks and containing folders using the operating
system's default handler (``os.startfile`` on Windows).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.logger import StructuredLogger
from app.models.service_models import ServiceResult
from app.services.base_service import BaseService


class NativeOpenerService(BaseService):
    """Launch files and folders with the OS default application.

    Parameters
    ----------
    logger:
        Structured logger instance.
    """

    def __init__(self, logger: StructuredLogger) -> None:
        super().__init__(logger)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_file(self, path: Path) -> ServiceResult:
        """Open *path* with the system-registered application.

        On Windows this delegates to ``os.startfile``; on other
        platforms it falls back to ``subprocess`` with ``xdg-open``
        or ``open``.

        Returns
        -------
        ServiceResult
            ``success=True`` if the OS accepted the open request.
        """
        if not path.exists():
            self._logger.warning("open_file called on missing path: %s", path)
            return ServiceResult(
                success=False,
                error=f"File not found: {path.name}",
                status_code=404,
            )

        return self._os_open(path)

    def open_folder(self, path: Path) -> ServiceResult:
        """Open the folder containing *path* in the system file manager.

        If *path* is a file, its parent directory is opened.
        If *path* is itself a directory, it is opened directly.
        """
        target: Path = path if path.is_dir() else path.parent

        if not target.exists():
            self._logger.warning(
                "open_folder called on missing directory: %s", target,
            )
            return ServiceResult(
                success=False,
                error=f"Directory not found: {target}",
                status_code=404,
            )

        return self._os_open(target)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _os_open(self, path: Path) -> ServiceResult:
        """Dispatch to the platform-appropriate open mechanism."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])  # noqa: S603
            else:
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S603

            self._logger.info("Opened with OS handler: %s", path)
            return ServiceResult(success=True)

        except FileNotFoundError:
            self._logger.error("OS handler not found for: %s", path)
            return ServiceResult(
                success=False,
                error=f"No application registered to open {path.suffix} files.",
                status_code=500,
            )
        except OSError as exc:
            self._logger.error("OS error opening %s: %s", path, exc)
            return ServiceResult(
                success=False,
                error="Could not open the file. Please try opening it manually.",
                status_code=500,
            )
