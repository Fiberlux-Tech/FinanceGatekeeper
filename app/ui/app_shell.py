"""Application Host Shell.

The top-level ``CTk`` window that orchestrates the entire application
lifecycle: login → host shell (sidebar + modules) → logout.

All dependencies are injected via the constructor.  The shell contains
no business logic — it delegates authentication to the ``LoginView``,
user provisioning to ``JITProvisioningService``, and module rendering
to the ``ModuleRegistry`` + ``SidebarNav``.
"""

from __future__ import annotations

from typing import Optional

import customtkinter as ctk

from app.auth import SessionManager
from app.config import AppConfig
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.services import ServiceContainer
from app.services.session_cache import SessionCacheService
from app.ui.components.status_bar import StatusBar
from app.ui.module_registry import ModuleRegistry
from app.ui.sidebar import SidebarNav
from app.ui.theme import (
    CONTENT_BG,
    LOGIN_WINDOW_HEIGHT,
    LOGIN_WINDOW_WIDTH,
    MAIN_WINDOW_HEIGHT,
    MAIN_WINDOW_WIDTH,
)

_SESSION_CHECK_INTERVAL_MS: int = 60_000  # 60 seconds


class AppShell(ctk.CTk):
    """Host Shell — the main application window.

    Lifecycle
    ---------
    1. On boot: displays the ``LoginView``.
    2. On successful login: builds the sidebar, content area, and
       status bar; switches to the default module.
    3. Module switching: caches frames (lazy creation).
    4. Logout: clears session, destroys module frames, returns to login.
    5. Periodic token refresh every 60 s via ``self.after()``.

    Parameters
    ----------
    config:
        Application configuration.
    db:
        Dual-database manager (Supabase + SQLite).
    session:
        Injectable session holder for the authenticated user.
    services:
        Fully-wired service container.
    session_cache:
        Encrypted session cache service for offline auth.
    registry:
        Module registry populated before shell launch.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        config: AppConfig,
        db: DatabaseManager,
        session: SessionManager,
        services: ServiceContainer,
        session_cache: SessionCacheService,
        registry: ModuleRegistry,
        logger: StructuredLogger,
    ) -> None:
        super().__init__()

        self._config = config
        self._db = db
        self._session = session
        self._services = services
        self._session_cache = session_cache
        self._registry = registry
        self._logger = logger

        # Module frame cache (module_id → CTkFrame)
        self._module_frames: dict[str, ctk.CTkFrame] = {}
        self._active_module_id: Optional[str] = None

        # Layout containers (created on demand)
        self._sidebar: Optional[SidebarNav] = None
        self._content_container: Optional[ctk.CTkFrame] = None
        self._status_bar: Optional[StatusBar] = None

        # Window defaults
        self.title("Finance Gatekeeper OS")
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        # Start with the login screen
        self._show_login()

    # ==================================================================
    # View transitions
    # ==================================================================

    def _show_login(self) -> None:
        """Display the login view and size the window appropriately."""
        self._clear_main_shell()
        self.geometry(f"{LOGIN_WINDOW_WIDTH}x{LOGIN_WINDOW_HEIGHT}")
        self.resizable(False, False)

        # Lazy import to avoid circular dependency at module level
        from app.ui.login_view import LoginView  # noqa: WPS433

        self._login_view = LoginView(
            parent=self,
            db=self._db,
            session=self._session,
            jit_service=self._services["jit_provisioning_service"],
            session_cache=self._session_cache,
            on_login_success=self._handle_login_success,
            logger=self._logger,
        )
        self._login_view.pack(fill="both", expand=True)

    def _show_main_shell(self) -> None:
        """Build and display the sidebar + content area + status bar."""
        self.geometry(f"{MAIN_WINDOW_WIDTH}x{MAIN_WINDOW_HEIGHT}")
        self.resizable(True, True)
        self.minsize(800, 500)

        # --- Sidebar ---
        user = self._session.get_current_user()
        self._sidebar = SidebarNav(
            parent=self,
            on_module_selected=self._switch_module,
            on_logout=self._handle_logout,
            session=self._session,
            logger=self._logger,
        )
        self._sidebar.pack(side="left", fill="y")

        # Register modules visible to the user's role
        for entry in self._registry.get_modules_for_role(user.role):
            self._sidebar.register_module(
                module_id=entry.module_id,
                display_name=entry.display_name,
                icon=entry.icon,
            )

        # --- Content container ---
        self._content_container = ctk.CTkFrame(self, fg_color=CONTENT_BG)
        self._content_container.pack(side="top", fill="both", expand=True)

        # --- Status bar ---
        self._status_bar = StatusBar(
            parent=self,
            db=self._db,
            logger=self._logger,
        )
        self._status_bar.pack(side="bottom", fill="x")

        # Activate default module
        default_id = self._registry.default_module_id
        if default_id:
            self._switch_module(default_id)

        # Start periodic session check
        self._check_session()

    # ==================================================================
    # Module switching
    # ==================================================================

    def _switch_module(self, module_id: str) -> None:
        """Activate a module: hide current frame, show (or create) target."""
        if module_id == self._active_module_id:
            return

        # Hide current
        if self._active_module_id and self._active_module_id in self._module_frames:
            self._module_frames[self._active_module_id].pack_forget()

        # Create or retrieve target frame
        if module_id not in self._module_frames:
            entry = self._registry.get_module(module_id)
            frame = entry.factory(self._content_container)
            self._module_frames[module_id] = frame

        self._module_frames[module_id].pack(fill="both", expand=True)
        self._active_module_id = module_id

        if self._sidebar:
            self._sidebar.set_active(module_id)

        self._logger.info("Switched to module: %s", module_id)

    # ==================================================================
    # Auth lifecycle
    # ==================================================================

    def _handle_login_success(self) -> None:
        """Called by ``LoginView`` after successful authentication."""
        # Remove login view
        if hasattr(self, "_login_view") and self._login_view is not None:
            self._login_view.destroy()
            self._login_view = None

        self._logger.info(
            "Login successful: %s",
            self._session.get_current_user().full_name,
        )
        self._show_main_shell()

    def _handle_logout(self) -> None:
        """Clear session state and return to the login screen."""
        full_name = (
            self._session.get_current_user().full_name
            if self._session.is_authenticated
            else "unknown"
        )
        self._session.clear()
        self._session_cache.clear_session()
        self._logger.info("User logged out: %s", full_name)
        self._show_login()

    def _clear_main_shell(self) -> None:
        """Destroy sidebar, content, status bar, and cached module frames."""
        for frame in self._module_frames.values():
            frame.destroy()
        self._module_frames.clear()
        self._active_module_id = None

        if self._sidebar:
            self._sidebar.destroy()
            self._sidebar = None
        if self._content_container:
            self._content_container.destroy()
            self._content_container = None
        if self._status_bar:
            self._status_bar.destroy()
            self._status_bar = None

    # ==================================================================
    # Session refresh
    # ==================================================================

    def _check_session(self) -> None:
        """Periodic check: refresh the access token if nearing expiry."""
        if not self._session.is_authenticated:
            return

        if self._session.is_token_expired and self._db.is_online:
            refresh_token: Optional[str] = self._session.refresh_token
            if refresh_token:
                try:
                    response = self._db.supabase.auth.refresh_session(refresh_token)
                    new_session = response.session  # gotrue.Session | None
                    if new_session is not None:
                        self._session.set_tokens(
                            access_token=new_session.access_token,
                            refresh_token=new_session.refresh_token,
                            expires_at=new_session.expires_at,
                        )
                        self._logger.info("Session token refreshed.")
                except Exception as exc:
                    self._logger.warning(
                        "Session refresh failed: %s. User may need to re-login.",
                        exc,
                    )

        # Schedule next check
        self.after(_SESSION_CHECK_INTERVAL_MS, self._check_session)
