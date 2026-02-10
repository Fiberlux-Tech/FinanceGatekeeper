"""
Just-in-Time User Provisioning Service.

Ensures that authenticated users (verified via Supabase JWT) are automatically
synchronized to the local database via the UserRepository.

Sync strategy:
    - Always sync metadata on every request (email, username, role).
    - Use UUID from JWT 'sub' claim for lookups (indexed).
    - Fail authentication if provisioning fails (strict mode).

Architectural notes:
    - All database access goes through UserRepository (offline-first).
    - No direct SQLAlchemy session usage or Flask globals.
    - Race condition handling preserved: if upsert fails, retry get_by_id.
"""

from __future__ import annotations

import logging
from typing import Optional

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
    reflects the latest JWT metadata (email, username, role).
    """

    def __init__(
        self,
        repo: UserRepository,
        logger: logging.Logger,
    ) -> None:
        super().__init__(logger)
        self._repo = repo

    def ensure_user_synced(
        self,
        user_id: str,
        email: str,
        username: str,
        role: str,
    ) -> User:
        """
        Ensure a user exists in the database and metadata is synchronized.

        This method is called on EVERY authenticated request to maintain
        database consistency with Supabase Auth JWT claims.

        Args:
            user_id: Supabase UUID from JWT 'sub' claim.
            email: Email from JWT 'email' claim.
            username: Username from JWT 'user_metadata.username'.
            role: Role string from JWT 'user_metadata.role'.

        Returns:
            The synchronized User model instance.

        Raises:
            JITProvisioningError: If database sync fails.

        Performance:
            - Best case (no changes): ~5ms (single SELECT by PK)
            - Worst case (update): ~15ms (SELECT + UPDATE + COMMIT)
            - First login: ~20ms (INSERT + COMMIT)
        """
        # Validate the role string against the enum
        try:
            validated_role: UserRole = UserRole(role)
        except ValueError:
            self._logger.warning(
                "JIT Provisioning: Invalid role '%s' for user %s. Defaulting to SALES.",
                role,
                username,
            )
            validated_role = UserRole.SALES

        try:
            return self._sync_user(user_id, email, username, validated_role)
        except JITProvisioningError:
            raise
        except Exception as exc:
            self._logger.error(
                "JIT Provisioning: Unexpected error syncing user %s. Error: %s",
                username,
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
        username: str,
        role: UserRole,
    ) -> User:
        """
        Core synchronisation logic. Separated for clarity.

        1. Look up user by ID.
        2. If missing, create via upsert (with race-condition retry).
        3. If present, detect and apply metadata changes.
        """
        existing_user: Optional[User] = self._repo.get_by_id(user_id)

        if existing_user is None:
            return self._provision_new_user(user_id, email, username, role)

        return self._sync_existing_user(existing_user, email, username, role)

    def _provision_new_user(
        self,
        user_id: str,
        email: str,
        username: str,
        role: UserRole,
    ) -> User:
        """
        Create a brand-new user record.

        Handles the race condition where two concurrent requests both
        try to create the same user: if the upsert fails, we retry the
        lookup. If the retry also returns None, we raise.
        """
        self._logger.info(
            "JIT Provisioning: Creating new user %s (ID: %s)", username, user_id,
        )

        new_user = User(
            id=user_id,
            email=email,
            username=username,
            role=role,
        )

        try:
            created_user: User = self._repo.upsert(new_user)
        except Exception as exc:
            # Possible race condition -- another request created the user
            self._logger.warning(
                "JIT Provisioning: Race condition detected for %s. "
                "Retrying lookup. Error: %s",
                username,
                exc,
            )

            retried_user: Optional[User] = self._repo.get_by_id(user_id)
            if retried_user is None:
                raise JITProvisioningError(
                    f"Failed to create user {username} due to integrity constraint",
                    original_error=exc,
                )

            self._logger.info(
                "JIT Provisioning: User %s found on retry after race condition.",
                username,
            )
            return retried_user

        self._logger.info(
            "JIT Provisioning: Successfully created user %s", username,
        )

        log_audit_event(
            logger=self._logger,
            action="JIT_CREATE",
            entity_type="User",
            entity_id=user_id,
            user_id=user_id,
            details={"email": email, "username": username, "role": str(role)},
        )

        return created_user

    def _sync_existing_user(
        self,
        user: User,
        email: str,
        username: str,
        role: UserRole,
    ) -> User:
        """
        Compare JWT claims against the stored user and apply any differences.

        Only performs a write if at least one field has changed.
        """
        changes: list[str] = []

        needs_update: bool = False
        updated_email: str = user.email
        updated_username: str = user.username
        updated_role: UserRole = user.role

        if user.email != email:
            changes.append(f"email: {user.email} -> {email}")
            updated_email = email
            needs_update = True

        if user.username != username:
            changes.append(f"username: {user.username} -> {username}")
            updated_username = username
            needs_update = True

        if user.role != role:
            changes.append(f"role: {user.role} -> {role}")
            updated_role = role
            needs_update = True

        if not needs_update:
            return user

        self._logger.info(
            "JIT Provisioning: Syncing metadata for %s (ID: %s). Changes: %s",
            username,
            user.id,
            ", ".join(changes),
        )

        updated_user = User(
            id=user.id,
            email=updated_email,
            username=updated_username,
            role=updated_role,
        )

        try:
            synced_user: User = self._repo.upsert(updated_user)
        except Exception as exc:
            raise JITProvisioningError(
                f"Failed to sync user {username}: duplicate email or username",
                original_error=exc,
            )

        self._logger.info(
            "JIT Provisioning: Successfully synced user %s", username,
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
