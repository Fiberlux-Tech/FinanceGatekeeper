"""
User Management Service.

Handles administrative user operations: listing, role updates, and password resets.
All Supabase Auth admin API calls go through the injected DatabaseManager.

Architectural notes:
    - Local database writes go through UserRepository (offline-first).
    - Supabase Auth metadata updates use self._db.supabase (admin API).
    - Both layers must stay in sync to prevent JIT provisioning from
      reverting admin changes on the user's next request.
"""

from __future__ import annotations

from typing import Optional

from app.auth import CurrentUser
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.enums import UserRole
from app.models.service_models import ServiceResult
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.base_service import BaseService
from app.utils.audit import log_audit_event


class UserService(BaseService):
    """Service layer for admin user management operations."""

    def __init__(
        self,
        repo: UserRepository,
        db: DatabaseManager,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._repo = repo
        self._db = db

    def get_all_users(self) -> ServiceResult:
        """
        Fetch all users for the admin dashboard.

        Returns a list of user dicts excluding sensitive fields.
        Delegates entirely to the repository layer which handles
        Supabase-first with SQLite fallback.
        """
        try:
            users: list[User] = self._repo.get_all()
            user_list: list[dict[str, object]] = [
                {
                    "id": user.id,
                    "full_name": user.full_name,
                    "email": user.email,
                    "role": str(user.role),
                }
                for user in users
            ]
            return ServiceResult(success=True, data=user_list)
        except Exception as exc:
            self._logger.error("Failed to fetch users: %s", exc)
            return ServiceResult(
                success=False,
                error=f"Database error fetching users: {exc}",
                status_code=500,
            )

    def update_user_role(
        self,
        user_id: str,
        new_role: str,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Update a user's role in the local database AND Supabase Auth metadata.

        CRITICAL: Must update Supabase so the JWT contains the correct role
        on next refresh. Without this, JIT provisioning reads the old role
        from the JWT and overwrites the admin's change.

        Args:
            user_id: Supabase UUID of the target user.
            new_role: One of 'SALES', 'FINANCE', 'ADMIN'.
            current_user: The authenticated admin performing the change.
        """
        # --- 0. RBAC: Only ADMIN can change roles ---
        if current_user.role != UserRole.ADMIN:
            return ServiceResult(
                success=False,
                error="Only ADMIN users can update user roles.",
                status_code=403,
            )

        # --- 1. Validate the role string against the enum ---
        try:
            validated_role: UserRole = UserRole(new_role)
        except ValueError:
            return ServiceResult(
                success=False,
                error=f"Invalid role specified: '{new_role}'. "
                       f"Must be one of: {', '.join(r.value for r in UserRole)}.",
                status_code=400,
            )

        # --- 2. Verify user exists ---
        user: Optional[User] = self._repo.get_by_id(user_id)
        if user is None:
            return ServiceResult(
                success=False,
                error="User not found.",
                status_code=404,
            )

        old_role: str = str(user.role)

        # --- 3. Update local database via repository ---
        try:
            updated_user: Optional[User] = self._repo.update_role(user_id, validated_role)
            if updated_user is None:
                return ServiceResult(
                    success=False,
                    error="Failed to update role in database.",
                    status_code=500,
                )
        except Exception as exc:
            self._logger.error("Repository update_role failed for %s: %s", user_id, exc)
            return ServiceResult(
                success=False,
                error=f"Could not update role: {exc}",
                status_code=500,
            )

        # --- 4. CRITICAL: Sync Supabase Auth user_metadata ---
        self._sync_supabase_metadata(
            user_id=user_id,
            full_name=updated_user.full_name,
            role=validated_role,
        )

        # --- 5. Audit trail ---
        log_audit_event(
            logger=self._logger,
            action="UPDATE_ROLE",
            entity_type="User",
            entity_id=user_id,
            user_id=current_user.id,
            details={
                "old_role": old_role,
                "new_role": str(validated_role),
                "performed_by": current_user.full_name,
            },
        )

        return ServiceResult(
            success=True,
            data={
                "message": f"Role for user {updated_user.full_name} updated to {validated_role}.",
            },
        )

    def reset_user_password(
        self,
        user_id: str,
        new_password: str,
        current_user: CurrentUser,
    ) -> ServiceResult:
        """
        Reset a user's password via the Supabase Admin API.

        The password is only stored in Supabase Auth -- the local database
        has no password column. This method verifies the user exists locally
        before making the remote call.

        Args:
            user_id: Supabase UUID of the target user.
            new_password: The new plaintext password (Supabase hashes it).
            current_user: The authenticated admin performing the reset.
        """
        # --- 0. RBAC: Only ADMIN can reset passwords ---
        if current_user.role != UserRole.ADMIN:
            return ServiceResult(
                success=False,
                error="Only ADMIN users can reset passwords.",
                status_code=403,
            )

        # --- 1. Verify user exists ---
        user: Optional[User] = self._repo.get_by_id(user_id)
        if user is None:
            return ServiceResult(
                success=False,
                error="User not found.",
                status_code=404,
            )

        # --- 2. Call Supabase Admin API ---
        try:
            self._db.supabase.auth.admin.update_user_by_id(
                user_id,
                {"password": new_password},
            )
        except RuntimeError:
            # DatabaseManager raises RuntimeError if Supabase is not initialized
            self._logger.error("Supabase client not initialized for password reset.")
            return ServiceResult(
                success=False,
                error="Supabase credentials not configured.",
                status_code=503,
            )
        except Exception as exc:
            self._logger.error("Password reset failed for %s: %s", user_id, exc)
            return ServiceResult(
                success=False,
                error=f"Could not reset password: {exc}",
                status_code=500,
            )

        # --- 3. Audit trail ---
        log_audit_event(
            logger=self._logger,
            action="RESET_PASSWORD",
            entity_type="User",
            entity_id=user_id,
            user_id=current_user.id,
            details={"performed_by": current_user.full_name},
        )

        self._logger.info("Password reset for user %s", user.full_name)
        return ServiceResult(
            success=True,
            data={"message": f"Password for user {user.full_name} successfully reset."},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sync_supabase_metadata(
        self,
        user_id: str,
        full_name: str,
        role: UserRole,
    ) -> None:
        """
        Push role/full_name into Supabase Auth user_metadata.

        This is a best-effort operation: if Supabase is unreachable the local
        database already holds the correct value and will be re-synced on the
        next successful request.
        """
        try:
            self._db.supabase.auth.admin.update_user_by_id(
                user_id,
                {
                    "user_metadata": {
                        "full_name": full_name,
                        "role": str(role),
                    }
                },
            )
            self._logger.info(
                "Updated Supabase metadata for %s: role=%s", full_name, role,
            )
        except RuntimeError:
            self._logger.warning(
                "Supabase client not initialized -- metadata not updated. "
                "Role change may be reverted by JIT provisioning on next request.",
            )
        except Exception as exc:
            self._logger.error(
                "Failed to update Supabase metadata for %s: %s", full_name, exc,
            )
