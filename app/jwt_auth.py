"""
Authentication Guard Decorator.

Provides a factory that produces a decorator for gating service-layer
functions behind an authenticated session.

Usage::

    from app.auth import SessionManager
    from app.jwt_auth import require_auth

    session = SessionManager()
    auth_guard = require_auth(session)

    @auth_guard
    def some_service_function() -> str:
        return "only reachable when logged in"
"""

from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from app.auth import SessionManager

P = ParamSpec("P")
R = TypeVar("R")


class AuthenticationError(RuntimeError):
    """Raised when a guarded function is called without an active session."""


def require_auth(session: SessionManager) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Return a decorator that enforces authentication via *session*.

    The returned decorator checks ``session.is_authenticated`` before
    every call to the wrapped function.  If no user is logged in, an
    :class:`AuthenticationError` is raised.

    Args:
        session: The injectable ``SessionManager`` that holds the
            current user state.

    Returns:
        A decorator suitable for wrapping service-layer callables.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not session.is_authenticated:
                raise AuthenticationError(
                    "Authentication required. Please log in before "
                    "performing this action."
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator
