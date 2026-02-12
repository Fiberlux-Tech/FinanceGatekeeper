"""
Authentication & Session State.

Provides an injectable ``SessionManager`` that holds the authenticated
user (``User`` model) for the lifetime of a single-user desktop session.

Usage::

    from app.auth import SessionManager
    from app.models.user import User

    session = SessionManager()
    session.set_current_user(User(
        id="abc-123",
        email="user@example.com",
        full_name="John Doe",
        role="ADMIN",
    ))
    user = session.get_current_user()
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.user import User

# Backward-compatible alias so existing ``from app.auth import CurrentUser``
# statements continue to work during the migration period.
CurrentUser = User


class SessionManager:
    """Injectable holder for the current authenticated user.

    Each instance maintains its own session state, eliminating the
    need for module-level globals.  Pass a single ``SessionManager``
    through your dependency-injection layer so every component shares
    the same session.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._current_user: Optional[User] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    def set_current_user(self, user: User) -> None:
        """Record *user* as the authenticated session user."""
        with self._lock:
            self._current_user = user

    def get_current_user(self) -> User:
        """Return the authenticated user.

        Raises:
            RuntimeError: If no user is currently authenticated.
        """
        with self._lock:
            if self._current_user is None:
                raise RuntimeError(
                    "No user is currently authenticated. Login required."
                )
            return self._current_user

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: int,
    ) -> None:
        """Store Supabase auth tokens for session refresh.

        Parameters
        ----------
        access_token:
            The short-lived JWT access token.
        refresh_token:
            The long-lived refresh token used to obtain new access tokens.
        expires_at:
            Unix timestamp (seconds) when the access token expires.
        """
        with self._lock:
            self._access_token = access_token
            self._refresh_token = refresh_token
            self._token_expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)

    @property
    def access_token(self) -> Optional[str]:
        """Return the current access token, or ``None`` if not set."""
        with self._lock:
            return self._access_token

    @property
    def refresh_token(self) -> Optional[str]:
        """Return the refresh token for session renewal."""
        with self._lock:
            return self._refresh_token

    @property
    def is_token_expired(self) -> bool:
        """``True`` when the access token has expired or was never set."""
        with self._lock:
            if self._token_expiry is None:
                return True
            return datetime.now(timezone.utc) >= (self._token_expiry - timedelta(seconds=30))

    def clear(self) -> None:
        """Remove the current user and tokens, ending the session."""
        with self._lock:
            self._current_user = None
            self._access_token = None
            self._refresh_token = None
            self._token_expiry = None

    @property
    def is_authenticated(self) -> bool:
        """``True`` when a user is currently logged in."""
        with self._lock:
            return self._current_user is not None
