"""Login View — Authentication Screen.

Presents a polished login form with Sign In / Request Access tabs,
authenticates against Supabase via ``AuthService``, falls back to an
encrypted offline session cache when no internet is available, and
provisions the user to the local SQLite database via JIT sync.

**Thin UI Rule**: This module contains ZERO business logic.  It
gathers inputs, delegates to ``AuthService``, and displays results.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from app.logger import StructuredLogger
from app.models.auth_models import AuthErrorCode
from app.services.auth_service import AuthService
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
    SUCCESS_TEXT,
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

    Delegates all authentication logic to the injected ``AuthService``:

    - Login (online + offline with password verification)
    - Registration (Supabase sign_up with validation)
    - Password reset
    - Rate limiting

    Parameters
    ----------
    parent:
        The root ``CTk`` window this frame belongs to.
    auth_service:
        Centralised authentication service encapsulating all auth logic.
    on_login_success:
        Callback invoked (on the main thread) after successful login.
    logger:
        Structured JSON logger for audit trail.
    """

    def __init__(
        self,
        parent: ctk.CTk,
        auth_service: AuthService,
        on_login_success: Callable[[], None],
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)

        self._parent: ctk.CTk = parent
        self._auth_service: AuthService = auth_service
        self._on_login_success: Callable[[], None] = on_login_success
        self._logger: StructuredLogger = logger

        # Active tab tracking
        self._active_tab: str = "sign_in"

        # Sign In widgets
        self._email_entry: Optional[ctk.CTkEntry] = None
        self._password_entry: Optional[ctk.CTkEntry] = None
        self._login_button: Optional[ctk.CTkButton] = None
        self._error_label: Optional[ctk.CTkLabel] = None

        # Rate-limit countdown
        self._countdown_label: Optional[ctk.CTkLabel] = None
        self._countdown_job: Optional[str] = None

        # Forgot Password widgets
        self._forgot_password_frame: Optional[ctk.CTkFrame] = None
        self._forgot_email_entry: Optional[ctk.CTkEntry] = None
        self._forgot_button: Optional[ctk.CTkButton] = None
        self._forgot_message_label: Optional[ctk.CTkLabel] = None

        # Request Access widgets
        self._ra_first_name_entry: Optional[ctk.CTkEntry] = None
        self._ra_last_name_entry: Optional[ctk.CTkEntry] = None
        self._ra_email_entry: Optional[ctk.CTkEntry] = None
        self._ra_password_entry: Optional[ctk.CTkEntry] = None
        self._ra_create_button: Optional[ctk.CTkButton] = None
        self._ra_error_label: Optional[ctk.CTkLabel] = None
        self._ra_success_label: Optional[ctk.CTkLabel] = None

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

        # Use grid to centre the card — row/column weights push it to the
        # middle, and the card resizes naturally when tabs change height.
        self.grid_rowconfigure(0, weight=1)      # top spacer
        self.grid_rowconfigure(1, weight=0)      # card row (natural size)
        self.grid_rowconfigure(2, weight=0)      # copyright row
        self.grid_rowconfigure(3, weight=1)      # bottom spacer
        self.grid_columnconfigure(0, weight=1)   # centre horizontally

        # Card container — fixed width, height grows with content
        self._card = ctk.CTkFrame(
            self,
            width=_CARD_WIDTH,
            fg_color=CONTENT_CARD_BG,
            corner_radius=16,
            border_width=1,
            border_color="#e0e0e0",
        )
        self._card.grid(row=1, column=0, pady=(0, PADDING_SM))

        inner = ctk.CTkFrame(self._card, fg_color="transparent")
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
            self,
            text="\u00A9 2025 Fiberlux Finanzas. All rights reserved.",
            font=("Segoe UI", 10),
            text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, pady=(PADDING_SM, 0))

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

        # Rate-limit countdown label (hidden by default)
        self._countdown_label = ctk.CTkLabel(
            parent,
            text="",
            font=FONT_SMALL,
            text_color=ERROR_TEXT,
        )
        # Not packed — shown during rate-limit lockout

        # Forgot Password link
        ctk.CTkButton(
            parent,
            text="Forgot Password?",
            font=("Segoe UI", 11),
            fg_color="transparent",
            hover_color="#f0f0f0",
            text_color=ACCENT_PRIMARY,
            height=28,
            corner_radius=CORNER_RADIUS,
            command=self._show_forgot_password,
        ).pack(pady=(PADDING_SM, 0))

        # Forgot Password inline form (hidden by default)
        self._forgot_password_frame = ctk.CTkFrame(parent, fg_color="transparent")

        ctk.CTkLabel(
            self._forgot_password_frame,
            text="Enter your email to receive a reset link:",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
        ).pack(fill="x", pady=(0, 4))

        self._forgot_email_entry = ctk.CTkEntry(
            self._forgot_password_frame,
            placeholder_text="name@fiberlux.pe",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._forgot_email_entry.pack(fill="x", pady=(0, PADDING_SM))

        self._forgot_button = ctk.CTkButton(
            self._forgot_password_frame,
            text="Send Reset Link",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=36,
            corner_radius=CORNER_RADIUS,
            command=self._handle_forgot_password,
        )
        self._forgot_button.pack(fill="x", pady=(0, PADDING_SM))

        self._forgot_message_label = ctk.CTkLabel(
            self._forgot_password_frame,
            text="",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
            wraplength=_CARD_WIDTH - 100,
        )
        self._forgot_message_label.pack(fill="x")
        # NOT packed yet — shown on "Forgot Password?" click

        # Key bindings
        self._email_entry.bind("<Return>", self._on_enter_key)
        self._password_entry.bind("<Return>", self._on_enter_key)

    def _build_request_access_tab(self, parent: ctk.CTkFrame) -> None:
        """Build the Request Access registration form."""
        # Name row — two side-by-side fields
        name_labels = ctk.CTkFrame(parent, fg_color="transparent")
        name_labels.pack(fill="x", pady=(PADDING_MD, 4))
        name_labels.grid_columnconfigure(0, weight=1)
        name_labels.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            name_labels,
            text="FIRST NAME",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            name_labels,
            text="LAST NAME",
            font=_LABEL_FONT,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(PADDING_SM, 0))

        name_row = ctk.CTkFrame(parent, fg_color="transparent")
        name_row.pack(fill="x", pady=(0, PADDING_MD))
        name_row.grid_columnconfigure(0, weight=1)
        name_row.grid_columnconfigure(1, weight=1)

        self._ra_first_name_entry = ctk.CTkEntry(
            name_row,
            placeholder_text="e.g. Juan",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._ra_first_name_entry.grid(row=0, column=0, sticky="ew")

        self._ra_last_name_entry = ctk.CTkEntry(
            name_row,
            placeholder_text="e.g. Perez",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._ra_last_name_entry.grid(
            row=0, column=1, sticky="ew", padx=(PADDING_SM, 0),
        )

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
        self._ra_create_button = ctk.CTkButton(
            parent,
            text="Create Account  \u2192",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_BUTTON_HEIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._handle_request_access,
        )
        self._ra_create_button.pack(fill="x", pady=(0, PADDING_SM))

        # Error label for Request Access (hidden by default)
        self._ra_error_label = ctk.CTkLabel(
            parent,
            text="",
            font=FONT_SMALL,
            text_color=ERROR_TEXT,
            wraplength=_CARD_WIDTH - 100,
        )
        self._ra_error_label.pack(fill="x")
        self._ra_error_label.pack_forget()

        # Success label for Request Access (hidden by default)
        self._ra_success_label = ctk.CTkLabel(
            parent,
            text="",
            font=FONT_SMALL,
            text_color=SUCCESS_TEXT,
            wraplength=_CARD_WIDTH - 100,
        )
        self._ra_success_label.pack(fill="x")
        self._ra_success_label.pack_forget()

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
        self._clear_ra_messages()

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
    # Event Handlers — Sign In
    # ------------------------------------------------------------------

    def _on_enter_key(self, event: tk.Event[tk.Misc]) -> None:
        """Trigger the login flow when the user presses Enter."""
        self._handle_login()

    def _handle_login(self) -> None:
        """Gather inputs, check rate limit, start background auth."""
        email = self._email_entry.get().strip()
        password = self._password_entry.get()

        if not email or not password:
            self._show_error("Please enter email and password.")
            return

        # Check rate limit before starting background thread
        is_locked, remaining = self._auth_service.check_rate_limit()
        if is_locked:
            self._show_error(
                f"Too many failed attempts. Please wait {remaining} seconds."
            )
            self._start_countdown(remaining)
            return

        self._set_loading(True)
        self._clear_error()

        threading.Thread(
            target=self._authenticate,
            args=(email, password),
            daemon=True,
        ).start()

    def _authenticate(self, email: str, password: str) -> None:
        """Background thread: delegate to AuthService.login().

        Runs off the main thread to keep the UI responsive.  All UI
        mutations are dispatched back via ``self.after(0, ...)``.
        """
        try:
            result = self._auth_service.login(email, password)

            if result.success:
                self.after(0, self._on_login_success)
            else:
                def show_login_result() -> None:
                    self._show_error(result.error_message or "Login failed.")
                    if result.error_code == AuthErrorCode.RATE_LIMITED:
                        _, remaining = self._auth_service.check_rate_limit()
                        self._start_countdown(remaining)
                self.after(0, show_login_result)
        except Exception as exc:
            error_msg = str(exc)
            self.after(
                0,
                lambda msg=error_msg: self._show_error(f"Login failed: {msg}"),
            )
        finally:
            self.after(0, lambda: self._set_loading(False))

    # ------------------------------------------------------------------
    # Event Handlers — Request Access (Registration)
    # ------------------------------------------------------------------

    def _handle_request_access(self) -> None:
        """Gather inputs, validate non-empty, start background registration."""
        first_name = self._ra_first_name_entry.get().strip()
        last_name = self._ra_last_name_entry.get().strip()
        email = self._ra_email_entry.get().strip()
        password = self._ra_password_entry.get()

        self._clear_ra_messages()

        if not all([first_name, last_name, email, password]):
            self._show_ra_error("All fields are required.")
            return

        self._set_ra_loading(True)
        threading.Thread(
            target=self._do_register,
            args=(first_name, last_name, email, password),
            daemon=True,
        ).start()

    def _do_register(
        self,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
    ) -> None:
        """Background thread: delegate to AuthService.register()."""
        try:
            result = self._auth_service.register(
                first_name, last_name, email, password,
            )

            def show_registration_result() -> None:
                if result.success:
                    self._ra_success_label.configure(
                        text="Account created! You can now sign in.",
                    )
                    self._ra_success_label.pack(fill="x")
                    # Clear form fields
                    self._ra_first_name_entry.delete(0, "end")
                    self._ra_last_name_entry.delete(0, "end")
                    self._ra_email_entry.delete(0, "end")
                    self._ra_password_entry.delete(0, "end")
                    # Auto-switch to Sign In tab after 3 seconds
                    self.after(3000, lambda: self._switch_tab("sign_in"))
                else:
                    self._show_ra_error(
                        result.error_message or "Registration failed."
                    )

            self.after(0, show_registration_result)
        except Exception as exc:
            error_msg = str(exc)
            self.after(
                0,
                lambda msg=error_msg: self._show_ra_error(
                    f"Registration failed: {msg}"
                ),
            )
        finally:
            self.after(0, lambda: self._set_ra_loading(False))

    # ------------------------------------------------------------------
    # Event Handlers — Forgot Password
    # ------------------------------------------------------------------

    def _show_forgot_password(self) -> None:
        """Toggle visibility of the Forgot Password inline form."""
        if self._forgot_password_frame.winfo_manager():
            self._forgot_password_frame.pack_forget()
        else:
            self._forgot_password_frame.pack(fill="x", pady=(PADDING_SM, 0))
            self._forgot_message_label.configure(text="")

    def _handle_forgot_password(self) -> None:
        """Delegate password reset to AuthService."""
        email = self._forgot_email_entry.get().strip()
        if not email:
            self._forgot_message_label.configure(
                text="Please enter your email address.",
                text_color=ERROR_TEXT,
            )
            return

        self._forgot_button.configure(text="Sending...", state="disabled")

        def do_reset() -> None:
            result = self._auth_service.request_password_reset(email)

            def show_reset_result() -> None:
                color = SUCCESS_TEXT if result.success else ERROR_TEXT
                self._forgot_message_label.configure(
                    text=result.error_message or "",
                    text_color=color,
                )
                self._forgot_button.configure(
                    text="Send Reset Link", state="normal",
                )

            self.after(0, show_reset_result)

        threading.Thread(target=do_reset, daemon=True).start()

    # ------------------------------------------------------------------
    # Rate-limit countdown
    # ------------------------------------------------------------------

    def _start_countdown(self, seconds: int) -> None:
        """Show a countdown timer for rate-limit lockout."""
        if self._countdown_job:
            self.after_cancel(self._countdown_job)

        self._login_button.configure(state="disabled")
        self._countdown_label.pack(fill="x")

        def tick(remaining: int) -> None:
            if remaining <= 0:
                self._countdown_label.pack_forget()
                self._login_button.configure(
                    state="normal", text="Sign In  \u2192",
                )
                self._countdown_job = None
                return
            self._countdown_label.configure(
                text=f"Please wait {remaining} seconds before trying again.",
            )
            self._login_button.configure(text=f"Sign In ({remaining}s)")
            self._countdown_job = self.after(1000, lambda: tick(remaining - 1))

        tick(seconds)

    # ------------------------------------------------------------------
    # UI Helper Methods
    # ------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        """Display a red error message below the login button."""
        if self._error_label is not None:
            self._error_label.configure(text=message)
            self._error_label.pack(fill="x")

    def _clear_error(self) -> None:
        """Hide the error label."""
        if self._error_label is not None:
            self._error_label.configure(text="")
            self._error_label.pack_forget()

    def _show_ra_error(self, message: str) -> None:
        """Display an error in the Request Access tab."""
        if self._ra_error_label is not None:
            self._ra_error_label.configure(text=message)
            self._ra_error_label.pack(fill="x")

    def _clear_ra_messages(self) -> None:
        """Hide both error and success labels in the Request Access tab."""
        if self._ra_error_label is not None:
            self._ra_error_label.configure(text="")
            self._ra_error_label.pack_forget()
        if self._ra_success_label is not None:
            self._ra_success_label.configure(text="")
            self._ra_success_label.pack_forget()

    def _set_loading(self, loading: bool) -> None:
        """Toggle the login button between normal and loading states.

        When *loading* is ``True`` the button text changes to
        "Signing in..." and becomes disabled so the user cannot
        double-submit.  When a countdown is active, the button state
        is managed by ``_start_countdown`` and must not be overridden.
        """
        if self._login_button is None:
            return
        if loading:
            self._login_button.configure(
                text="Signing in...",
                state="disabled",
            )
        else:
            # Don't re-enable if a rate-limit countdown is running
            if self._countdown_job is not None:
                return
            self._login_button.configure(
                text="Sign In  \u2192",
                state="normal",
            )

    def _set_ra_loading(self, loading: bool) -> None:
        """Toggle the Create Account button between normal and loading states."""
        if self._ra_create_button is None:
            return
        if loading:
            self._ra_create_button.configure(
                text="Creating account...",
                state="disabled",
            )
        else:
            self._ra_create_button.configure(
                text="Create Account  \u2192",
                state="normal",
            )
