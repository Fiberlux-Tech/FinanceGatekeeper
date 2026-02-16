"""Application Host Shell.

The top-level ``CTk`` window that orchestrates the entire application
lifecycle: login → host shell (sidebar + modules) → logout.

All dependencies are injected via the constructor.  The shell contains
no business logic — it delegates authentication to the ``LoginView``,
user provisioning to ``JITProvisioningService``, and module rendering
to the ``ModuleRegistry`` + ``SidebarNav``.
"""

from __future__ import annotations

import threading
from typing import Optional

import customtkinter as ctk

from app import __version__ as _APP_VERSION
from app.auth import SessionManager
from app.config import AppConfig
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.models.auth_models import AuthErrorCode, AuthResult
from app.models.file_models import ResolvedPaths
from app.services import ServiceContainer
from app.services.auth_service import AuthService
from app.services.file_watcher import FileWatcherService
from app.services.sync_worker import SyncWorkerService
from app.ui.login_view import LoginView
from app.ui.module_registry import ModuleRegistry
from app.ui.sidebar import SidebarNav
from app.ui.theme import (
    CONTENT_BG,
    FONT_BODY,
    LOGIN_WINDOW_HEIGHT,
    LOGIN_WINDOW_WIDTH,
    MAIN_WINDOW_HEIGHT,
    MAIN_WINDOW_WIDTH,
    TEXT_SECONDARY,
)
from app.ui.views.path_config_view import PathConfigView

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
        registry: ModuleRegistry,
        logger: StructuredLogger,
    ) -> None:
        super().__init__()

        self._config = config
        self._db = db
        self._session = session
        self._services = services
        self._registry = registry
        self._logger = logger

        # Module frame cache (module_id → CTkFrame)
        self._module_frames: dict[str, ctk.CTkFrame] = {}
        self._active_module_id: Optional[str] = None
        self._session_check_job: Optional[str] = None

        # Layout containers (created on demand)
        self._sidebar: Optional[SidebarNav] = None
        self._content_container: Optional[ctk.CTkFrame] = None

        # Window defaults
        self.title("Finance Gatekeeper OS")
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        # Graceful shutdown on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start with the login screen
        self._show_login()

    # ==================================================================
    # View transitions
    # ==================================================================

    def _show_login(self) -> None:
        """Display the login view and size the window appropriately."""
        self._clear_main_shell()

        # Start at a comfortable size; allow resizing down to the card minimum
        self.geometry(f"{MAIN_WINDOW_WIDTH}x{MAIN_WINDOW_HEIGHT}")
        self.resizable(True, True)
        self.minsize(LOGIN_WINDOW_WIDTH, LOGIN_WINDOW_HEIGHT)

        self._login_view = LoginView(
            parent=self,
            auth_service=self._services["auth_service"],
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
            db=self._db,
            version=_APP_VERSION,
        )
        self._sidebar.pack(side="left", fill="y")

        # Register modules visible to the user's role
        role_modules = self._registry.get_modules_for_role(user.role)
        for entry in role_modules:
            self._sidebar.register_module(
                module_id=entry.module_id,
                display_name=entry.display_name,
                icon=entry.icon,
            )

        # --- Content container ---
        self._content_container = ctk.CTkFrame(self, fg_color=CONTENT_BG)
        self._content_container.pack(side="top", fill="both", expand=True)

        # Activate default module — or show a placeholder when none are available
        if role_modules:
            default_id = self._registry.default_module_id
            if default_id:
                self._switch_module(default_id)
        else:
            self._logger.warning(
                "No modules available for role '%s'.", user.role,
            )
            ctk.CTkLabel(
                self._content_container,
                text=(
                    "No modules available for your role. "
                    "Contact your administrator."
                ),
                font=FONT_BODY,
                text_color=TEXT_SECONDARY,
            ).place(relx=0.5, rely=0.5, anchor="center")

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
            try:
                entry = self._registry.get_module(module_id)
            except KeyError:
                self._logger.error(
                    "Cannot switch to unregistered module: %s", module_id,
                )
                return
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
        """Called by ``LoginView`` after successful authentication.

        If the file watcher service could not be created at startup
        (no SharePoint path found), the user is shown an inline path
        configuration view before the main shell loads.
        """
        # Remove login view
        if hasattr(self, "_login_view") and self._login_view is not None:
            self._login_view.destroy()
            self._login_view = None

        self._logger.info(
            "Login successful: %s",
            self._session.get_current_user().full_name,
        )

        watcher = self._services.get("file_watcher_service")
        if watcher is None:
            self._show_path_config()
        else:
            self._show_main_shell()
            self._start_file_watcher()
            self._start_sync_worker()

    def _show_path_config(self) -> None:
        """Display the inline path configuration view."""
        self._path_config_view = PathConfigView(
            parent=self,
            path_discovery=self._services["path_discovery_service"],
            app_settings=self._services["app_settings_service"],
            on_path_configured=self._handle_path_configured,
            on_skip=self._handle_path_skip,
            logger=self._logger,
        )
        self._path_config_view.pack(fill="both", expand=True)

    def _handle_path_configured(self, resolved: ResolvedPaths) -> None:
        """Called after the user selects and confirms a valid path.

        Creates the ``FileWatcherService`` on the fly, injects it into
        the service container, and proceeds to the main shell.
        """
        if hasattr(self, "_path_config_view") and self._path_config_view is not None:
            self._path_config_view.destroy()
            self._path_config_view = None

        file_watcher = FileWatcherService(
            inbox_path=resolved.inbox,
            file_guards=self._services["file_guards_service"],
            config=self._config,
            logger=self._logger,
        )
        self._services["file_watcher_service"] = file_watcher  # type: ignore[typeddict-item]

        self._logger.info(
            "SharePoint path configured: %s", resolved.sharepoint_root,
        )
        self._show_main_shell()
        self._start_file_watcher()
        self._start_sync_worker()

    def _handle_path_skip(self) -> None:
        """User chose to skip path configuration — proceed without watcher."""
        if hasattr(self, "_path_config_view") and self._path_config_view is not None:
            self._path_config_view.destroy()
            self._path_config_view = None

        self._logger.info("User skipped SharePoint path configuration.")
        self._show_main_shell()

    def _handle_logout(self) -> None:
        """Delegate logout to AuthService and return to login screen."""
        self._stop_file_watcher()
        self._stop_sync_worker()
        if self._session_check_job is not None:
            self.after_cancel(self._session_check_job)
            self._session_check_job = None
        auth_service: AuthService = self._services["auth_service"]
        auth_service.logout()
        self._show_login()

    def _clear_main_shell(self) -> None:
        """Destroy sidebar, content, status bar, and cached module frames."""
        for frame in self._module_frames.values():
            frame.destroy()
        self._module_frames.clear()
        self._active_module_id = None

        if hasattr(self, "_path_config_view") and self._path_config_view is not None:
            self._path_config_view.destroy()
            self._path_config_view = None
        if self._sidebar:
            self._sidebar.destroy()
            self._sidebar = None
        if self._content_container:
            self._content_container.destroy()
            self._content_container = None

    # ==================================================================
    # Session refresh
    # ==================================================================

    def _check_session(self) -> None:
        """Periodic check: refresh the access token via AuthService.

        Dispatches the network call to a background thread so the UI
        event loop is never blocked by Supabase round-trips (M-24).
        The callback ``_handle_session_refresh_result`` is scheduled
        back on the main thread via ``self.after()``.
        """
        if not self._session.is_authenticated:
            return

        auth_service: AuthService = self._services["auth_service"]

        def _refresh_in_background() -> None:
            result = auth_service.refresh_session_token()
            # Schedule result handling on the main (UI) thread.
            self.after(0, self._handle_session_refresh_result, result)

        thread = threading.Thread(
            target=_refresh_in_background,
            name="session-refresh",
            daemon=True,
        )
        thread.start()

    def _handle_session_refresh_result(self, result: AuthResult) -> None:
        """Process the token-refresh result on the main thread.

        Distinguishes auth errors (expired/revoked refresh token →
        forced logout) from transient network errors (silently retry
        next cycle).

        Parameters
        ----------
        result:
            The ``AuthResult`` returned by ``refresh_session_token``.
        """
        if not result.success and result.error_code == AuthErrorCode.SESSION_EXPIRED:
            self._logger.warning("Session expired. Forcing logout.")
            auth_service: AuthService = self._services["auth_service"]
            auth_service.logout()
            self._show_login()
            # Show the session-expired message after login view is built
            self.after(100, self._show_session_expired_message)
            return

        # Schedule next check
        self._session_check_job = self.after(_SESSION_CHECK_INTERVAL_MS, self._check_session)

    def _show_session_expired_message(self) -> None:
        """Show session expired message on the login view."""
        if hasattr(self, "_login_view") and self._login_view is not None:
            self._login_view.show_message(
                "Your session has expired. Please sign in again."
            )

    # ==================================================================
    # File watcher lifecycle
    # ==================================================================

    def _start_file_watcher(self) -> None:
        """Start the inbox file watcher if available."""
        watcher = self._services.get("file_watcher_service")
        if isinstance(watcher, FileWatcherService):
            watcher.start()

    def _stop_file_watcher(self) -> None:
        """Stop the inbox file watcher if running."""
        watcher = self._services.get("file_watcher_service")
        if isinstance(watcher, FileWatcherService):
            watcher.stop()

    def _start_sync_worker(self) -> None:
        """Start the background sync worker if available."""
        worker = self._services.get("sync_worker")
        if isinstance(worker, SyncWorkerService):
            worker.start()

    def _stop_sync_worker(self) -> None:
        """Stop the background sync worker if running."""
        worker = self._services.get("sync_worker")
        if isinstance(worker, SyncWorkerService):
            worker.stop()

    # ==================================================================
    # Window close
    # ==================================================================

    def _on_close(self) -> None:
        """Gracefully shut down background threads before destroying."""
        self._stop_file_watcher()
        self._stop_sync_worker()
        if self._session_check_job is not None:
            self.after_cancel(self._session_check_job)
            self._session_check_job = None
        self.destroy()
