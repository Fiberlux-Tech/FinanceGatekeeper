"""
Authentication & Session State.

Provides a ``CurrentUser`` Pydantic model and an injectable
``SessionManager`` that holds the authenticated user for the
lifetime of a single-user desktop session.

Usage::

    from app.auth import CurrentUser, SessionManager

    session = SessionManager()
    session.set_current_user(CurrentUser(
        id="abc-123",
        email="user@example.com",
        username="jdoe",
        role="ADMIN",
    ))
    user = session.get_current_user()
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """Immutable snapshot of the authenticated user's identity."""

    id: str
    email: str
    username: str
    role: Literal["SALES", "FINANCE", "ADMIN"]


class SessionManager:
    """Injectable holder for the current authenticated user.

    Each instance maintains its own session state, eliminating the
    need for module-level globals.  Pass a single ``SessionManager``
    through your dependency-injection layer so every component shares
    the same session.
    """

    def __init__(self) -> None:
        self._current_user: Optional[CurrentUser] = None

    def set_current_user(self, user: CurrentUser) -> None:
        """Record *user* as the authenticated session user."""
        self._current_user = user

    def get_current_user(self) -> CurrentUser:
        """Return the authenticated user.

        Raises:
            RuntimeError: If no user is currently authenticated.
        """
        if self._current_user is None:
            raise RuntimeError(
                "No user is currently authenticated. Login required."
            )
        return self._current_user

    def clear(self) -> None:
        """Remove the current user, ending the session."""
        self._current_user = None

    @property
    def is_authenticated(self) -> bool:
        """``True`` when a user is currently logged in."""
        return self._current_user is not None
