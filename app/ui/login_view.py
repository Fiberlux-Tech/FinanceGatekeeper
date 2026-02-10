"""Login View — Authentication Screen.

Presents a polished login form with Sign In / Request Access tabs,
authenticates against Supabase, falls back to an encrypted offline
session cache when no internet is available, and provisions the user
to the local SQLite database via JIT sync.

**Thin UI Rule**: This module contains ZERO business logic.  It
gathers inputs, delegates to injected services, and displays results.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from app.auth import CurrentUser, SessionManager
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.services.jit_provisioning import JITProvisioningService
from app.services.session_cache import SessionCacheService
from app.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRIMARY,
    CONTENT_BG,
    CONTENT_CARD_BG,
    CORNER_RADIUS,
    ERROR_TEXT,
    FONT_BODY,
    FONT_BUTTON,
    FONT_SMALL,
    INPUT_BG,
    INPUT_BORDER,
    PADDING_LG,
    PADDING_MD,
    PADDING_SM,
    TEXT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CARD_WIDTH: int = 420
_TAB_HEIGHT: int = 42
_INPUT_HEIGHT: int = 44
_BUTTON_HEIGHT: int = 48
_LABEL_FONT: tuple[str, int, str] = ("Segoe UI", 11, "bold")
_BRAND_ICON_SIZE: int = 56


class LoginView(ctk.CTkFrame):
    """Full-screen login frame with Sign In / Request Access tabs.

    Delegates all authentication logic to injected services:

    - ``DatabaseManager`` for Supabase client access.
    - ``SessionManager`` to store the authenticated user.
    - ``JITProvisioningService`` to sync the user to local SQLite.
    - ``SessionCacheService`` for encrypted offline session fallback.
    - ``StructuredLogger`` for audit-grade JSON logging.

    Parameters
    ----------
    parent:
        The root ``CTk`` window this frame belongs to.
    db:
        Initialised database manager (Supabase + SQLite).
    session:
        The session manager that holds current user state.
    jit_service:
        Service for just-in-time user provisioning to local DB.
    session_cache:
        Service for caching/loading encrypted session data offline.
    on_login_success:
        Callback invoked (on the main thread) after successful login.
    logger:
        Structured JSON logger for audit trail.
    """

    def __init__(
        self,
        parent: ctk.CTk,
        db: DatabaseManager,
        session: SessionManager,
        jit_service: JITProvisioningService,
        session_cache: SessionCacheService,
        on_login_success: Callable[[], None],
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)

        self._parent: ctk.CTk = parent
        self._db: DatabaseManager = db
        self._session: SessionManager = session
        self._jit_service: JITProvisioningService = jit_service
        self._session_cache: SessionCacheService = session_cache
        self._on_login_success: Callable[[], None] = on_login_success
        self._logger: StructuredLogger = logger

        # Active tab tracking
        self._active_tab: str = "sign_in"

        # Widget references (populated by _build_ui)
        self._email_entry: Optional[ctk.CTkEntry] = None
        self._password_entry: Optional[ctk.CTkEntry] = None
        self._login_button: Optional[ctk.CTkButton] = None
        self._error_label: Optional[ctk.CTkLabel] = None

        # Request Access widgets
        self._ra_name_entry: Optional[ctk.CTkEntry] = None
        self._ra_email_entry: Optional[ctk.CTkEntry] = None
        self._ra_password_entry: Optional[ctk.CTkEntry] = None

        # Tab buttons
        self._sign_in_tab: Optional[ctk.CTkButton] = None
        self._request_tab: Optional[ctk.CTkButton] = None

        # Tab content frames
        self._sign_in_frame: Optional[ctk.CTkFrame] = None
        self._request_frame: Optional[ctk.CTkFrame] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the complete login screen matching the Fiberlux design."""
        self.pack(fill="both", expand=True)

        # Scrollable wrapper so the card is always reachable on small windows
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=CONTENT_BG,
            scrollbar_button_hover_color=INPUT_BORDER,
        )
        self._scroll.pack(fill="both", expand=True)

        # Centering wrapper — keeps the card centred horizontally
        center_wrapper = ctk.CTkFrame(self._scroll, fg_color="transparent")
        center_wrapper.pack(fill="x", expand=True, pady=(40, 20))

        # Card container — fixed width, height grows with content
        card = ctk.CTkFrame(
            center_wrapper,
            width=_CARD_WIDTH,
            fg_color=CONTENT_CARD_BG,
            corner_radius=16,
            border_width=1,
            border_color="#e0e0e0",
        )
        card.pack(anchor="center")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=36, pady=28)

        # -- Brand icon (shield) --
        icon_frame = ctk.CTkFrame(
            inner,
            width=_BRAND_ICON_SIZE,
            height=_BRAND_ICON_SIZE,
            corner_radius=14,
            fg_color=ACCENT_PRIMARY,
        )
        icon_frame.pack(pady=(0, 12))
        icon_frame.pack_propagate(False)

        ctk.CTkLabel(
            icon_frame,
            text="\u2713",
            font=("Segoe UI", 24, "bold"),
            text_color=TEXT_LIGHT,
        ).place(relx=0.5, rely=0.5, anchor="center")

        # -- Brand name --
        ctk.CTkLabel(
            inner,
            text="Fiberlux Finanzas",
            font=("Segoe UI", 22, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(pady=(0, 2))

        # -- Subtitle --
        ctk.CTkLabel(
            inner,
            text="Secure Operation Gatekeeper",
            font=("Segoe UI", 12),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(0, PADDING_LG))

        # -- Tab bar --
        tab_bar = ctk.CTkFrame(inner, fg_color="transparent", height=_TAB_HEIGHT)
        tab_bar.pack(fill="x", pady=(0, PADDING_MD))
        tab_bar.pack_propagate(False)
        tab_bar.grid_columnconfigure(0, weight=1)
        tab_bar.grid_columnconfigure(1, weight=1)

        self._sign_in_tab = ctk.CTkButton(
            tab_bar,
            text="Sign In",
            font=("Segoe UI", 13, "bold"),
            fg_color="transparent",
            hover_color="#f0f0f0",
            text_color=ACCENT_PRIMARY,
            height=_TAB_HEIGHT,
            corner_radius=0,
            border_width=2,
            border_color=ACCENT_PRIMARY,
            command=lambda: self._switch_tab("sign_in"),
        )
        self._sign_in_tab.grid(row=0, column=0, sticky="nsew")

        self._request_tab = ctk.CTkButton(
            tab_bar,
            text="Request Access",
            font=("Segoe UI", 13),
            fg_color="transparent",
            hover_color="#f0f0f0",
            text_color=TEXT_SECONDARY,
            height=_TAB_HEIGHT,
            corner_radius=0,
            border_width=1,
            border_color=INPUT_BORDER,
            command=lambda: self._switch_tab("request_access"),
        )
        self._request_tab.grid(row=0, column=1, sticky="nsew")

        # -- Sign In content --
        self._sign_in_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self._build_sign_in_tab(self._sign_in_frame)

        # -- Request Access content --
        self._request_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self._build_request_access_tab(self._request_frame)

        # Show sign-in tab by default
        self._sign_in_frame.pack(fill="both", expand=True)

        # -- Footer --
        ctk.CTkLabel(
            inner,
            text="Offline Mode: Sign in online first to enable offline access.",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
        ).pack(side="bottom", pady=(PADDING_SM, 0))

        # -- Copyright --
        ctk.CTkLabel(
            self._scroll,
            text="\u00A9 2025 Fiberlux Finanzas. All rights reserved.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(PADDING_SM, PADDING_LG))

    def _build_sign_in_tab(self, parent: ctk.CTkFrame) -> None:
        """Build the Sign In form fields inside the given parent frame."""
        # Email
        ctk.CTkLabel(
            parent,
            text="EMAIL ADDRESS",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(PADDING_MD, 4))

        self._email_entry = ctk.CTkEntry(
            parent,
            placeholder_text="name@fiberlux.pe",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._email_entry.pack(fill="x", pady=(0, PADDING_MD))

        # Password
        ctk.CTkLabel(
            parent,
            text="PASSWORD",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self._password_entry = ctk.CTkEntry(
            parent,
            placeholder_text="\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            show="*",
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._password_entry.pack(fill="x", pady=(0, PADDING_LG))

        # Sign In button
        self._login_button = ctk.CTkButton(
            parent,
            text="Sign In  \u2192",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_BUTTON_HEIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._handle_login,
        )
        self._login_button.pack(fill="x", pady=(0, PADDING_SM))

        # Error label (hidden by default)
        self._error_label = ctk.CTkLabel(
            parent,
            text="",
            font=FONT_SMALL,
            text_color=ERROR_TEXT,
            wraplength=_CARD_WIDTH - 100,
        )
        self._error_label.pack(fill="x")
        self._error_label.pack_forget()

        # Help text
        ctk.CTkLabel(
            parent,
            text="Don't have an account? Ask your manager to invite you.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(PADDING_SM, 0))

        # Key bindings
        self._email_entry.bind("<Return>", self._on_enter_key)
        self._password_entry.bind("<Return>", self._on_enter_key)

    def _build_request_access_tab(self, parent: ctk.CTkFrame) -> None:
        """Build the Request Access form (UI-only, no backend yet)."""
        # Full Name
        ctk.CTkLabel(
            parent,
            text="FULL NAME",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(PADDING_MD, 4))

        self._ra_name_entry = ctk.CTkEntry(
            parent,
            placeholder_text="e.g. Juan Perez",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._ra_name_entry.pack(fill="x", pady=(0, PADDING_MD))

        # Email
        ctk.CTkLabel(
            parent,
            text="EMAIL ADDRESS",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self._ra_email_entry = ctk.CTkEntry(
            parent,
            placeholder_text="name@fiberlux.pe",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._ra_email_entry.pack(fill="x", pady=(0, PADDING_MD))

        # Password
        ctk.CTkLabel(
            parent,
            text="PASSWORD",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self._ra_password_entry = ctk.CTkEntry(
            parent,
            placeholder_text="\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            show="*",
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._ra_password_entry.pack(fill="x", pady=(0, PADDING_LG))

        # Create Account button
        ctk.CTkButton(
            parent,
            text="Create Account  \u2192",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_BUTTON_HEIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._handle_request_access,
        ).pack(fill="x", pady=(0, PADDING_SM))

        # Info text
        ctk.CTkLabel(
            parent,
            text="Your registration will be audited by the admin team.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).pack(pady=(PADDING_SM, 0))

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def _switch_tab(self, tab: str) -> None:
        """Switch between Sign In and Request Access tabs."""
        if tab == self._active_tab:
            return
        self._active_tab = tab
        self._clear_error()

        if tab == "sign_in":
            self._request_frame.pack_forget()
            self._sign_in_frame.pack(fill="both", expand=True)
            # Active tab style
            self._sign_in_tab.configure(
                text_color=ACCENT_PRIMARY,
                border_color=ACCENT_PRIMARY,
                border_width=2,
                font=("Segoe UI", 13, "bold"),
            )
            self._request_tab.configure(
                text_color=TEXT_SECONDARY,
                border_color=INPUT_BORDER,
                border_width=1,
                font=("Segoe UI", 13),
            )
        else:
            self._sign_in_frame.pack_forget()
            self._request_frame.pack(fill="both", expand=True)
            self._request_tab.configure(
                text_color=ACCENT_PRIMARY,
                border_color=ACCENT_PRIMARY,
                border_width=2,
                font=("Segoe UI", 13, "bold"),
            )
            self._sign_in_tab.configure(
                text_color=TEXT_SECONDARY,
                border_color=INPUT_BORDER,
                border_width=1,
                font=("Segoe UI", 13),
            )

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    def _on_enter_key(self, event: tk.Event[tk.Misc]) -> None:
        """Trigger the login flow when the user presses Enter."""
        self._handle_login()

    def _handle_login(self) -> None:
        """Gather inputs, validate, start background auth."""
        email = self._email_entry.get().strip()
        password = self._password_entry.get()

        if not email or not password:
            self._show_error("Please enter email and password.")
            return

        self._set_loading(True)
        self._clear_error()
        threading.Thread(
            target=self._authenticate,
            args=(email, password),
            daemon=True,
        ).start()

    def _handle_request_access(self) -> None:
        """Placeholder for the Request Access flow (not yet implemented)."""
        self._logger.info("Request Access clicked (not yet implemented).")

    # ------------------------------------------------------------------
    # Background Authentication
    # ------------------------------------------------------------------

    def _authenticate(self, email: str, password: str) -> None:
        """Background thread: Supabase auth -> JIT -> session cache.

        Runs off the main thread to keep the UI responsive.  All UI
        mutations are dispatched back via ``self.after(0, ...)``.
        """
        try:
            # 1. Try Supabase auth
            response = self._db.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
            user_data = response.user
            session_data = response.session
            user_metadata = user_data.user_metadata or {}

            # 2. Build CurrentUser from JWT
            current_user = CurrentUser(
                id=user_data.id,
                email=user_data.email or email,
                full_name=user_metadata.get("full_name", email.split("@")[0]),
                role=user_metadata.get("role", "SALES"),
            )

            # 3. Set session + tokens
            self._session.set_current_user(current_user)
            self._session.set_tokens(
                access_token=session_data.access_token,
                refresh_token=session_data.refresh_token,
                expires_at=session_data.expires_at,
            )

            # 4. JIT provisioning (sync to local SQLite)
            self._jit_service.ensure_user_synced(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
            )

            # 5. Cache session for offline use
            self._session_cache.cache_session(
                user_id=current_user.id,
                email=current_user.email,
                full_name=current_user.full_name,
                role=current_user.role,
                refresh_token=session_data.refresh_token,
            )

            self._logger.info(
                "User authenticated: %s (role: %s)",
                current_user.full_name,
                current_user.role,
            )
            self.after(0, self._on_login_success)

        except RuntimeError:
            # Supabase not available -- try offline fallback
            self.after(0, lambda: self._try_offline_login(email))
        except Exception as exc:
            error_msg = str(exc)
            self._logger.warning("Login failed: %s", error_msg)
            self.after(
                0,
                lambda msg=error_msg: self._show_error(f"Login failed: {msg}"),
            )
        finally:
            self.after(0, lambda: self._set_loading(False))

    # ------------------------------------------------------------------
    # Offline Fallback
    # ------------------------------------------------------------------

    def _try_offline_login(self, email: str) -> None:
        """Fallback: check encrypted session cache.

        If the session cache contains a valid entry for the given email,
        restore the session and proceed.  Otherwise, show an error
        explaining that online login is required first.
        """
        cached = self._session_cache.load_cached_session()
        if cached and cached.email == email:
            current_user = CurrentUser(
                id=cached.user_id,
                email=cached.email,
                full_name=cached.full_name,
                role=cached.role,
            )
            self._session.set_current_user(current_user)
            self._logger.warning(
                "Offline login: %s from encrypted cache.", email,
            )
            self._on_login_success()
        else:
            self._show_error(
                "No internet connection. "
                "Sign in online first to enable offline access."
            )

    # ------------------------------------------------------------------
    # UI Helper Methods
    # ------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        """Display a red error message below the login button."""
        self._error_label.configure(text=message)
        self._error_label.pack(fill="x")

    def _clear_error(self) -> None:
        """Hide the error label."""
        if self._error_label is not None:
            self._error_label.configure(text="")
            self._error_label.pack_forget()

    def _set_loading(self, loading: bool) -> None:
        """Toggle the login button between normal and loading states.

        When *loading* is ``True`` the button text changes to
        "Signing in..." and becomes disabled so the user cannot
        double-submit.
        """
        if loading:
            self._login_button.configure(
                text="Signing in...",
                state="disabled",
            )
        else:
            self._login_button.configure(
                text="Sign In  \u2192",
                state="normal",
            )
