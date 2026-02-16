"""
File Archival Service.

Handles the approve/reject file-archival workflow:

1. **Approve** — verify readiness, re-hash for chain of custody, rename with
   transaction ID prefix, encrypt via DPAPI-protected Fernet key, and move
   to ``02_ARCHIVE_APPROVED/{year}/{BU}/``.

2. **Reject** — identical safety checks and rename, but targets
   ``03_ARCHIVE_REJECTED/{year}/{BU}/`` and skips encryption.

Both paths enforce the Chain of Custody mandate (CLAUDE.md Section 5):
the SHA-256 hash is recomputed immediately before archival and compared
against the expected digest passed by the caller.

Encryption uses a Fernet symmetric key that is itself protected by Windows
DPAPI (``CryptProtectData``), ensuring only the same Windows user account
that created the key can decrypt archived files.  On non-Windows platforms
the key is stored as plain base64 in ``app_settings`` (acceptable for
development environments only).
"""

from __future__ import annotations

import base64
import shutil
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import AppConfig
from app.logger import StructuredLogger
from app.models.enums import BusinessUnit, FileStatus
from app.models.service_models import ServiceResult
from app.services.app_settings_service import AppSettingsService
from app.services.base_service import BaseService
from app.services.file_guards import FileGuardsService
from app.services.path_discovery import PathDiscoveryService

_SETTINGS_KEY_ENCRYPTION: str = "archive_encryption_key"


class FileArchivalService(BaseService):
    """Manages the approve/reject file-archival workflow.

    Parameters
    ----------
    file_guards:
        File-readiness and integrity service (lock check, steady state, SHA-256).
    path_discovery:
        SharePoint root and folder resolution service.
    app_settings:
        Persistent key-value store for infrastructure state (encryption key).
    config:
        Application configuration (archive folder names).
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        file_guards: FileGuardsService,
        path_discovery: PathDiscoveryService,
        app_settings: AppSettingsService,
        config: AppConfig,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._file_guards = file_guards
        self._path_discovery = path_discovery
        self._app_settings = app_settings
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def archive_approved(
        self,
        source_path: Path,
        transaction_id: str,
        business_unit: BusinessUnit,
        expected_sha256: str,
    ) -> ServiceResult[dict[str, str]]:
        """Execute the full approve-archive sequence.

        Sequence: verify readiness -> re-hash -> rename -> encrypt -> move.

        Parameters
        ----------
        source_path:
            Absolute path to the Excel file currently in the inbox.
        transaction_id:
            Unique transaction identifier used as filename prefix.
        business_unit:
            Determines the target subdirectory under the year folder.
        expected_sha256:
            SHA-256 hex digest recorded at ingestion time.  The file is
            re-hashed and compared to detect tampering.

        Returns
        -------
        ServiceResult
            On success: ``data={'archived_path': str, 'sha256': str}``.
            On failure: ``success=False`` with a descriptive ``error``.
        """
        archive_base = self._resolve_archive_base(approved=True)
        if archive_base is None:
            return ServiceResult(
                success=False,
                error=(
                    "SharePoint root is not configured. Cannot determine the "
                    "approved-archive folder path."
                ),
                status_code=500,
            )

        return self._archive_file(
            source_path=source_path,
            transaction_id=transaction_id,
            business_unit=business_unit,
            expected_sha256=expected_sha256,
            archive_base=archive_base,
            encrypt=True,
        )

    def archive_rejected(
        self,
        source_path: Path,
        transaction_id: str,
        business_unit: BusinessUnit,
        expected_sha256: str,
    ) -> ServiceResult[dict[str, str]]:
        """Execute the reject-archive sequence.

        Identical to :meth:`archive_approved` except the file is moved
        to ``03_ARCHIVE_REJECTED`` and is **not** encrypted.

        Parameters
        ----------
        source_path:
            Absolute path to the Excel file currently in the inbox.
        transaction_id:
            Unique transaction identifier used as filename prefix.
        business_unit:
            Determines the target subdirectory under the year folder.
        expected_sha256:
            SHA-256 hex digest recorded at ingestion time.

        Returns
        -------
        ServiceResult
            On success: ``data={'archived_path': str, 'sha256': str}``.
            On failure: ``success=False`` with a descriptive ``error``.
        """
        archive_base = self._resolve_archive_base(approved=False)
        if archive_base is None:
            return ServiceResult(
                success=False,
                error=(
                    "SharePoint root is not configured. Cannot determine the "
                    "rejected-archive folder path."
                ),
                status_code=500,
            )

        return self._archive_file(
            source_path=source_path,
            transaction_id=transaction_id,
            business_unit=business_unit,
            expected_sha256=expected_sha256,
            archive_base=archive_base,
            encrypt=False,
        )

    # ------------------------------------------------------------------
    # Core archival pipeline
    # ------------------------------------------------------------------

    def _archive_file(
        self,
        source_path: Path,
        transaction_id: str,
        business_unit: BusinessUnit,
        expected_sha256: str,
        archive_base: Path,
        encrypt: bool,
    ) -> ServiceResult[dict[str, str]]:
        """Shared implementation for both approve and reject workflows.

        Steps
        -----
        1. Check file readiness (not locked, not syncing).
        2. Re-compute SHA-256 and compare against ``expected_sha256``.
        3. Build the target directory ``{archive_base}/{year}/{BU}/``.
        4. Construct the new filename ``{transaction_id}_{original_name}``.
        5. Optionally encrypt the file in-place (Fernet + DPAPI).
        6. Move the file to the target directory.
        7. Return the archived path and verified hash.
        """
        # --- Step 1: File readiness ---
        try:
            check_result = self._file_guards.check_file_status(source_path)
        except Exception as exc:
            self._logger.error(
                "File status check failed for %s: %s", source_path.name, exc,
            )
            return ServiceResult(
                success=False,
                error=f"File readiness check failed: {exc}",
                status_code=500,
            )

        if check_result.status != FileStatus.READY:
            self._logger.warning(
                "File not ready for archival: %s — %s",
                source_path.name,
                check_result.message,
            )
            return ServiceResult(
                success=False,
                error=check_result.message,
                status_code=409,
            )

        # --- Step 2: Chain-of-custody hash verification ---
        try:
            current_sha256: str = self._file_guards.compute_sha256(source_path)
        except FileNotFoundError:
            return ServiceResult(
                success=False,
                error=f"File not found: {source_path}",
                status_code=404,
            )
        except PermissionError:
            return ServiceResult(
                success=False,
                error=(
                    f"Cannot read file (permission denied): {source_path.name}. "
                    "Please close the file and try again."
                ),
                status_code=423,
            )
        except OSError as exc:
            return ServiceResult(
                success=False,
                error=f"Failed to compute file hash: {exc}",
                status_code=500,
            )

        if current_sha256 != expected_sha256:
            self._logger.error(
                "Chain-of-custody violation for %s: expected=%s, actual=%s",
                source_path.name,
                expected_sha256,
                current_sha256,
            )
            return ServiceResult(
                success=False,
                error=(
                    "Chain-of-custody violation: the file has been modified "
                    "since it was ingested. Re-scan the inbox and try again."
                ),
                status_code=409,
            )

        # --- Step 3: Build target directory ---
        year_str: str = str(datetime.now().year)
        target_dir: Path = archive_base / year_str / business_unit.value

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._logger.error(
                "Failed to create archive directory %s: %s", target_dir, exc,
            )
            return ServiceResult(
                success=False,
                error=f"Cannot create archive directory: {exc}",
                status_code=500,
            )

        # --- Step 4: Build target filename ---
        original_name: str = source_path.name
        new_filename: str = f"{transaction_id}_{original_name}"

        # --- Step 5: Optional encryption ---
        file_to_move: Path = source_path
        if encrypt:
            try:
                file_to_move = self._encrypt_file(source_path)
                # Update filename to include the .enc extension
                new_filename = f"{transaction_id}_{original_name}.enc"
            except Exception as exc:
                self._logger.error(
                    "Encryption failed for %s: %s", source_path.name, exc,
                )
                return ServiceResult(
                    success=False,
                    error=f"File encryption failed: {exc}",
                    status_code=500,
                )

        # --- Step 6: Move file ---
        target_path: Path = target_dir / new_filename
        try:
            shutil.move(str(file_to_move), str(target_path))
        except PermissionError:
            self._logger.error(
                "Permission denied moving %s to %s",
                file_to_move.name,
                target_path,
            )
            return ServiceResult(
                success=False,
                error=(
                    f"Cannot move file to archive (permission denied). "
                    f"Please close the file and verify folder permissions."
                ),
                status_code=423,
            )
        except OSError as exc:
            self._logger.error(
                "Failed to move %s to %s: %s",
                file_to_move.name,
                target_path,
                exc,
            )
            return ServiceResult(
                success=False,
                error=f"File move failed: {exc}",
                status_code=500,
            )

        # --- Step 7: Success ---
        action: str = "approved" if encrypt else "rejected"
        self._logger.info(
            "File archived (%s): %s -> %s [sha256=%s]",
            action,
            original_name,
            target_path,
            current_sha256,
        )

        return ServiceResult(
            success=True,
            data={
                "archived_path": str(target_path),
                "sha256": current_sha256,
            },
        )

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_archive_base(self, approved: bool) -> Path | None:
        """Resolve the archive base directory from stored SharePoint root.

        Parameters
        ----------
        approved:
            ``True`` for the approved archive, ``False`` for rejected.

        Returns
        -------
        Path or None
            The resolved archive base path, or ``None`` if SharePoint root
            is not configured.
        """
        sharepoint_root: str | None = self._app_settings.get_sharepoint_root()
        if sharepoint_root is None:
            return None

        folder_name: str = (
            self._config.ARCHIVE_APPROVED_FOLDER_NAME
            if approved
            else self._config.ARCHIVE_REJECTED_FOLDER_NAME
        )
        return Path(sharepoint_root) / folder_name

    # ------------------------------------------------------------------
    # Encryption (DPAPI + Fernet)
    # ------------------------------------------------------------------

    def _get_or_create_fernet_key(self) -> bytes:
        """Retrieve or generate a Fernet key, protected by Windows DPAPI.

        Key lifecycle:
            - On first call, a new Fernet key is generated.
            - On Windows, the key is encrypted with DPAPI before storage,
              ensuring only the same Windows user account can decrypt it.
            - On non-Windows platforms, the key is stored as plain base64
              (development environments only).
            - The encrypted/encoded key is persisted in ``app_settings``
              under the ``archive_encryption_key`` key.

        Returns
        -------
        bytes
            The raw Fernet key (URL-safe base64-encoded 32-byte key).

        Raises
        ------
        RuntimeError
            If the stored key cannot be decrypted (e.g. different Windows
            user account) or if key generation/storage fails.
        """
        stored_value: str | None = self._app_settings.get(_SETTINGS_KEY_ENCRYPTION)

        if stored_value is not None:
            return self._decode_stored_key(stored_value)

        return self._generate_and_store_key()

    def _decode_stored_key(self, stored_value: str) -> bytes:
        """Decode a previously stored encryption key.

        Parameters
        ----------
        stored_value:
            Base64-encoded key (plain on non-Windows, DPAPI-encrypted on
            Windows).

        Returns
        -------
        bytes
            The raw Fernet key bytes.

        Raises
        ------
        RuntimeError
            If decryption fails.
        """
        try:
            raw_stored: bytes = base64.b64decode(stored_value)
        except Exception as exc:
            raise RuntimeError(
                f"Stored encryption key is corrupted (base64 decode failed): {exc}"
            ) from exc

        if sys.platform == "win32":
            try:
                import win32crypt  # noqa: WPS433 — Windows-only import

                decrypted: bytes = win32crypt.CryptUnprotectData(
                    raw_stored, None, None, None, 0,
                )[1]
                return decrypted
            except ImportError as exc:
                raise RuntimeError(
                    "win32crypt module is required on Windows for DPAPI "
                    "decryption but is not installed. Install pywin32."
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"DPAPI decryption failed — the key may have been "
                    f"created by a different Windows user account: {exc}"
                ) from exc
        else:
            # Non-Windows: key was stored as plain base64
            return raw_stored

    def _generate_and_store_key(self) -> bytes:
        """Generate a new Fernet key and persist it in app_settings.

        Returns
        -------
        bytes
            The newly generated Fernet key bytes.

        Raises
        ------
        RuntimeError
            If the key cannot be stored.
        """
        key: bytes = Fernet.generate_key()

        if sys.platform == "win32":
            try:
                import win32crypt  # noqa: WPS433 — Windows-only import

                encrypted: bytes = win32crypt.CryptProtectData(
                    key,
                    "FinanceGatekeeper Archive Key",
                    None,
                    None,
                    None,
                    0,
                )
                stored_value: str = base64.b64encode(encrypted).decode("utf-8")
            except ImportError as exc:
                raise RuntimeError(
                    "win32crypt module is required on Windows for DPAPI "
                    "encryption but is not installed. Install pywin32."
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"DPAPI encryption of Fernet key failed: {exc}"
                ) from exc
        else:
            # Non-Windows: store as plain base64
            stored_value = base64.b64encode(key).decode("utf-8")

        success: bool = self._app_settings.set(_SETTINGS_KEY_ENCRYPTION, stored_value)
        if not success:
            raise RuntimeError(
                "Failed to persist the encryption key in app_settings. "
                "Check database connectivity."
            )

        self._logger.info("New archive encryption key generated and stored.")
        return key

    def _encrypt_file(self, source_path: Path) -> Path:
        """Encrypt file contents using Fernet symmetric encryption.

        Reads the source file, encrypts its contents, writes the ciphertext
        to ``{source_path}.enc``, and deletes the original plaintext file.

        Parameters
        ----------
        source_path:
            Path to the plaintext file to encrypt.

        Returns
        -------
        Path
            Path to the newly created encrypted file (``*.enc``).

        Raises
        ------
        RuntimeError
            If the encryption key cannot be retrieved or generated.
        FileNotFoundError
            If ``source_path`` does not exist.
        PermissionError
            If the file is locked or cannot be read/written.
        OSError
            If any file I/O operation fails.
        """
        key: bytes = self._get_or_create_fernet_key()
        fernet = Fernet(key)

        data: bytes = source_path.read_bytes()
        encrypted_data: bytes = fernet.encrypt(data)

        encrypted_path: Path = source_path.with_suffix(source_path.suffix + ".enc")
        encrypted_path.write_bytes(encrypted_data)

        source_path.unlink()

        self._logger.debug(
            "File encrypted: %s -> %s", source_path.name, encrypted_path.name,
        )
        return encrypted_path
