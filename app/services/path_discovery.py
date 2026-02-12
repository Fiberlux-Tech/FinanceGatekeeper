"""
SharePoint / OneDrive Path Discovery Service.

Resolves the local sync root for the SharePoint document library that
contains the ``01_INBOX``, ``02_ARCHIVE_APPROVED``, and
``03_ARCHIVE_REJECTED`` folders.

Resolution cascade:
    1. ``SHAREPOINT_ROOT_OVERRIDE`` env var (manual override).
    2. User-configured path stored in ``app_settings`` (SQLite).
    3. Windows Registry (``HKCU\\Software\\Microsoft\\OneDrive``).
    4. ``%OneDriveCommercial%`` environment variable.
    5. ``FileNotFoundError`` with a human-readable message.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.logger import StructuredLogger
from app.models.file_models import ResolvedPaths
from app.services.base_service import BaseService


class PathDiscoveryService(BaseService):
    """Locate the local SharePoint sync folder on this machine.

    Parameters
    ----------
    config:
        Application configuration (provides folder names and override).
    logger:
        Structured logger instance.
    """

    def __init__(self, config: AppConfig, logger: StructuredLogger) -> None:
        super().__init__(logger)
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, stored_root: Optional[str] = None) -> ResolvedPaths:
        """Discover and validate the SharePoint root.

        Parameters
        ----------
        stored_root:
            Previously user-configured path from ``app_settings``.
            Checked after the env-var override but before the Windows
            Registry and ``%OneDriveCommercial%`` fallbacks.

        Returns
        -------
        ResolvedPaths
            Validated paths for inbox, archives, and detected BU folders.

        Raises
        ------
        FileNotFoundError
            If the SharePoint root cannot be located or the required
            inbox folder does not exist within it.
        """
        root = (
            self._try_config_override()
            or self._try_stored_root(stored_root)
            or self._try_registry()
            or self._try_environment()
        )

        if root is None:
            raise FileNotFoundError(
                "Could not locate the SharePoint sync folder. "
                "Set SHAREPOINT_ROOT_OVERRIDE in your .env file to the "
                "local path that contains the 01_Inbox directory."
            )

        self._logger.info("SharePoint root resolved: %s", root)
        return self._validate_root(root)

    def resolve_from_explicit_root(self, root_path: str) -> ResolvedPaths:
        """Validate an explicitly provided SharePoint root path.

        Used by the path configuration UI when the user manually selects
        a folder.  Runs the same ``_validate_root`` pipeline as the
        normal discovery cascade.

        Parameters
        ----------
        root_path:
            Absolute filesystem path to the SharePoint sync folder.

        Returns
        -------
        ResolvedPaths
            Validated paths if the root contains the required inbox.

        Raises
        ------
        FileNotFoundError
            If the path does not exist or the inbox folder is missing.
        """
        root = Path(root_path)
        if not root.is_dir():
            raise FileNotFoundError(
                f"The specified path does not exist: {root}"
            )
        self._logger.info("Validating explicit SharePoint root: %s", root)
        return self._validate_root(root)

    # ------------------------------------------------------------------
    # Discovery strategies
    # ------------------------------------------------------------------

    def _try_config_override(self) -> Optional[Path]:
        """Return the manual override path if configured and valid."""
        override = self._config.SHAREPOINT_ROOT_OVERRIDE.strip()
        if not override:
            return None

        candidate = Path(override)
        if candidate.is_dir():
            self._logger.info("Using SHAREPOINT_ROOT_OVERRIDE: %s", candidate)
            return candidate

        self._logger.warning(
            "SHAREPOINT_ROOT_OVERRIDE is set but the directory does not "
            "exist: %s",
            candidate,
        )
        return None

    def _try_stored_root(self, stored_root: Optional[str]) -> Optional[Path]:
        """Return the user-stored path if it exists and is a directory.

        Parameters
        ----------
        stored_root:
            Value read from ``app_settings``.  May be ``None`` or empty.
        """
        if not stored_root or not stored_root.strip():
            return None
        candidate = Path(stored_root.strip())
        if candidate.is_dir():
            self._logger.info("Using stored SharePoint root: %s", candidate)
            return candidate
        self._logger.warning(
            "Stored SharePoint root no longer exists: %s", candidate,
        )
        return None

    def _try_registry(self) -> Optional[Path]:
        """Read the OneDrive Business sync root from the Windows Registry."""
        if sys.platform != "win32":
            return None

        import winreg  # noqa: WPS433 — Windows-only import

        key_path = r"Software\Microsoft\OneDrive\Accounts\Business1"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                user_folder, _ = winreg.QueryValueEx(key, "UserFolder")
        except OSError:
            self._logger.debug(
                "OneDrive Business registry key not found at HKCU\\%s",
                key_path,
            )
            return None

        root = Path(str(user_folder))
        if not root.is_dir():
            self._logger.debug(
                "Registry points to non-existent directory: %s", root,
            )
            return None

        return self._scan_for_inbox(root)

    def _try_environment(self) -> Optional[Path]:
        """Check the ``OneDriveCommercial`` environment variable."""
        env_value = os.environ.get("OneDriveCommercial", "").strip()
        if not env_value:
            return None

        root = Path(env_value)
        if not root.is_dir():
            self._logger.debug(
                "%%OneDriveCommercial%% points to non-existent directory: %s",
                root,
            )
            return None

        return self._scan_for_inbox(root)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _scan_for_inbox(self, onedrive_root: Path) -> Optional[Path]:
        """Walk one level of children under *onedrive_root* for the inbox.

        OneDrive Business syncs each SharePoint library as a subfolder.
        We look for the library folder that contains ``01_Inbox``.
        """
        inbox_name = self._config.INBOX_FOLDER_NAME

        # Direct check: the root itself may contain the inbox.
        if (onedrive_root / inbox_name).is_dir():
            return onedrive_root

        # One-level scan: check immediate subdirectories.
        try:
            for child in onedrive_root.iterdir():
                if child.is_dir() and (child / inbox_name).is_dir():
                    self._logger.info(
                        "Found inbox inside library subfolder: %s", child,
                    )
                    return child
        except PermissionError:
            self._logger.warning(
                "Permission denied scanning OneDrive root: %s",
                onedrive_root,
            )

        self._logger.debug(
            "No '%s' folder found under %s", inbox_name, onedrive_root,
        )
        return None

    def _validate_root(self, root: Path) -> ResolvedPaths:
        """Verify the expected folder structure and enumerate BU folders.

        Raises
        ------
        FileNotFoundError
            If the inbox directory does not exist under *root*.
        """
        inbox = root / self._config.INBOX_FOLDER_NAME
        if not inbox.is_dir():
            raise FileNotFoundError(
                f"SharePoint root found at {root}, but the "
                f"'{self._config.INBOX_FOLDER_NAME}' folder is missing."
            )

        archive_approved = root / self._config.ARCHIVE_APPROVED_FOLDER_NAME
        archive_rejected = root / self._config.ARCHIVE_REJECTED_FOLDER_NAME

        if not archive_approved.is_dir():
            self._logger.warning(
                "Archive approved folder not found — will be created on "
                "first approval: %s",
                archive_approved,
            )
        if not archive_rejected.is_dir():
            self._logger.warning(
                "Archive rejected folder not found — will be created on "
                "first rejection: %s",
                archive_rejected,
            )

        self._logger.info(
            "Path discovery complete. Inbox: %s", inbox,
        )

        return ResolvedPaths(
            sharepoint_root=root,
            inbox=inbox,
            archive_approved=archive_approved,
            archive_rejected=archive_rejected,
        )
