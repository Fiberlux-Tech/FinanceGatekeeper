"""
Just-in-Time User Provisioning Service.

Ensures that authenticated users (verified via Supabase JWT) are automatically
synchronized to the local database via the UserRepository.

Sync strategy:
    - Sync non-privileged metadata on every login (email, full_name).
    - NEVER sync the ``role`` field from external sources (C-1 fix).
    - Use UUID from JWT 'sub' claim for lookups (indexed).
    - Fail authentication if provisioning fails (strict mode).

Architectural notes:
    - All database access goes through UserRepository (offline-first).
    - No direct SQLAlchemy session usage or Flask globals.
    - Race condition handling preserved: if upsert fails, retry get_by_id.
"""

from __future__ import annotations

from typing import Optional

from app.logger import StructuredLogger
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.base_service import BaseService
from app.utils.audit import log_audit_event


class JITProvisioningError(Exception):
    """Custom exception for JIT provisioning failures."""

    def __init__(self, message: str, original_error: Optional[Exception] = None) -> None:
        self.message: str = message
        self.original_error: Optional[Exception] = original_error
        super().__init__(self.message)


class JITProvisioningService(BaseService):
    """
    Service that synchronises Supabase Auth JWT claims to the local user table.

    Called on every authenticated request to ensure the local database
    reflects the latest JWT metadata (email, full_name).
    """

    def __init__(
        self,
        repo: UserRepository,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._repo = repo

    def ensure_user_synced(
        self,
        user_id: str,
        email: str,
        full_name: str,
    ) -> User:
        """Ensure a user exists in the database and metadata is synchronised.

        This method is called after every successful authentication to
        keep the local database in sync with identity metadata.

        **Security (C-1)**: New users are always provisioned with
        ``SALES``.  Existing users retain whatever role is stored in
        the ``profiles`` table.  Role escalation is only possible
        through explicit admin action via the ``service_role`` key.

        Args:
            user_id: Supabase UUID from JWT ``sub`` claim.
            email: Email from JWT ``email`` claim.
            full_name: Full name from JWT ``user_metadata.full_name``.

        Returns:
            The synchronised User model instance.

        Raises:
            JITProvisioningError: If database sync fails.
        """
        try:
            return self._sync_user(user_id, email, full_name)
        except JITProvisioningError:
            raise
        except Exception as exc:
            self._logger.error(
                "JIT Provisioning: Unexpected error syncing user %s. Error: %s",
                full_name,
                exc,
                exc_info=True,
            )
            raise JITProvisioningError(
                f"Unexpected error during user provisioning: {exc}",
                original_error=exc,
            )

    # ------------------------------------------------------------------
    # Private implementation
    # ------------------------------------------------------------------

    def _sync_user(
        self,
        user_id: str,
        email: str,
        full_name: str,
    ) -> User:
        """Core synchronisation logic.

        1. Look up user by ID.
        2. If missing, create via upsert with role=SALES (defence-in-depth).
        3. If present, sync email/full_name but **never** overwrite role.
        """
        existing_user: Optional[User] = self._repo.get_by_id(user_id)

        if existing_user is None:
            return self._provision_new_user(user_id, email, full_name)

        return self._sync_existing_user(existing_user, email, full_name)

    def _provision_new_user(
        self,
        user_id: str,
        email: str,
        full_name: str,
    ) -> User:
        """Create a brand-new user record with role=SALES.

        SECURITY (C-1): New users are **always** provisioned with
        ``UserRole.SALES``, matching the database trigger in
        ``20250102000000_harden_role_assignment.sql``.  The role
        parameter is intentionally absent — no external source may
        influence the initial role.

        Handles the race condition where two concurrent requests both
        try to create the same user: if the upsert fails, we retry the
        lookup.  If the retry also returns None, we raise.
        """
        self._logger.info(
            "JIT Provisioning: Creating new user %s (ID: %s)", full_name, user_id,
        )

        new_user = User(
            id=user_id,
            email=email,
            full_name=full_name,
            role=UserRole.SALES,
        )

        try:
            created_user: User = self._repo.upsert(new_user)
        except Exception as exc:
            # Possible race condition -- another request created the user
            self._logger.warning(
                "JIT Provisioning: Race condition detected for %s. "
                "Retrying lookup. Error: %s",
                full_name,
                exc,
            )

            retried_user: Optional[User] = self._repo.get_by_id(user_id)
            if retried_user is None:
                raise JITProvisioningError(
                    f"Failed to create user {full_name} due to integrity constraint",
                    original_error=exc,
                )

            self._logger.info(
                "JIT Provisioning: User %s found on retry after race condition.",
                full_name,
            )
            return retried_user

        self._logger.info(
            "JIT Provisioning: Successfully created user %s", full_name,
        )

        log_audit_event(
            logger=self._logger,
            action="JIT_CREATE",
            entity_type="User",
            entity_id=user_id,
            user_id=user_id,
            details={"email": email, "full_name": full_name, "role": str(UserRole.SALES)},
        )

        return created_user

    def _sync_existing_user(
        self,
        user: User,
        email: str,
        full_name: str,
    ) -> User:
        """Compare identity metadata and apply non-privileged changes.

        SECURITY (C-1): The ``role`` field is **never** overwritten
        during JIT sync.  Only ``email`` and ``full_name`` are
        synchronised from the authentication response.  Role changes
        must go through the explicit ``UserRepository.update_role()``
        path (admin-only, guarded by ``service_role`` key).
        """
        changes: list[str] = []

        needs_update: bool = False
        updated_email: str = user.email
        updated_full_name: str = user.full_name

        if user.email != email:
            changes.append(f"email: {user.email} -> {email}")
            updated_email = email
            needs_update = True

        if user.full_name != full_name:
            changes.append(f"full_name: {user.full_name} -> {full_name}")
            updated_full_name = full_name
            needs_update = True

        # SECURITY (C-1): Role is intentionally NOT compared or synced.
        # The authoritative role lives in the profiles table and is
        # managed exclusively via admin operations.

        if not needs_update:
            return user

        self._logger.info(
            "JIT Provisioning: Syncing metadata for %s (ID: %s). Changes: %s",
            full_name,
            user.id,
            ", ".join(changes),
        )

        updated_user = User(
            id=user.id,
            email=updated_email,
            full_name=updated_full_name,
            role=user.role,  # Preserve DB-authoritative role
        )

        try:
            synced_user: User = self._repo.upsert(updated_user)
        except Exception as exc:
            raise JITProvisioningError(
                f"Failed to sync user {full_name}: duplicate email or full_name",
                original_error=exc,
            )

        self._logger.info(
            "JIT Provisioning: Successfully synced user %s", full_name,
        )

        log_audit_event(
            logger=self._logger,
            action="JIT_SYNC",
            entity_type="User",
            entity_id=user.id,
            user_id=user.id,
            details={"changes": changes},
        )

        return synced_user
