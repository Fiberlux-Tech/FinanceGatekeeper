"""Path Configuration View — First-Run SharePoint Folder Setup.

Inline view shown after login when automatic SharePoint path detection
fails and no user-configured path exists.  Prompts the user to browse
to their local SharePoint sync folder, validates the structure, and
persists the selection for future sessions.

**Thin UI Rule**: All validation and persistence is delegated to
``PathDiscoveryService`` and ``AppSettingsService``.
"""

from __future__ import annotations

import threading
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from app.logger import StructuredLogger
from app.models.file_models import ResolvedPaths
from app.services.app_settings_service import AppSettingsService
from app.services.path_discovery import PathDiscoveryService
from app.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRIMARY,
    CONTENT_BG,
    CONTENT_CARD_BG,
    CORNER_RADIUS,
    ERROR_TEXT,
    FONT_BODY,
    FONT_BUTTON,
    FONT_HEADING,
    FONT_LABEL,
    FONT_SMALL,
    INPUT_BG,
    INPUT_BORDER,
    PADDING_LG,
    PADDING_MD,
    PADDING_SM,
    SUCCESS_TEXT,
    TAB_BORDER,
    TEXT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CARD_WIDTH: int = 520
_INPUT_HEIGHT: int = 44
_BUTTON_HEIGHT: int = 48
_BROWSE_BTN_WIDTH: int = 100


class PathConfigView(ctk.CTkFrame):
    """Inline path configuration screen shown when no SharePoint root is found.

    Parameters
    ----------
    parent:
        Parent widget (typically the ``AppShell`` root).
    path_discovery:
        Service used to validate the selected folder structure.
    app_settings:
        Service used to persist the selected folder path.
    on_path_configured:
        Callback invoked with validated ``ResolvedPaths`` on success.
    on_skip:
        Callback invoked when the user chooses to skip configuration.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        path_discovery: PathDiscoveryService,
        app_settings: AppSettingsService,
        on_path_configured: Callable[[ResolvedPaths], None],
        on_skip: Callable[[], None],
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)
        self._path_discovery = path_discovery
        self._app_settings = app_settings
        self._on_path_configured = on_path_configured
        self._on_skip = on_skip
        self._logger = logger

        # Widget references
        self._path_entry: Optional[ctk.CTkEntry] = None
        self._confirm_btn: Optional[ctk.CTkButton] = None
        self._error_label: Optional[ctk.CTkLabel] = None
        self._success_label: Optional[ctk.CTkLabel] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the path configuration card."""
        # Grid centering — same pattern as LoginView
        self.grid_rowconfigure(0, weight=1)      # top spacer
        self.grid_rowconfigure(1, weight=0)      # card row
        self.grid_rowconfigure(2, weight=1)      # bottom spacer
        self.grid_columnconfigure(0, weight=1)   # centre horizontally

        # Card container
        card = ctk.CTkFrame(
            self,
            width=_CARD_WIDTH,
            fg_color=CONTENT_CARD_BG,
            corner_radius=16,
            border_width=1,
            border_color=TAB_BORDER,
        )
        card.grid(row=1, column=0)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=36, pady=28)

        # -- Heading --
        ctk.CTkLabel(
            inner,
            text="Configure SharePoint Folder",
            font=FONT_HEADING,
            text_color=TEXT_PRIMARY,
        ).pack(pady=(0, PADDING_SM))

        # -- Instruction text --
        ctk.CTkLabel(
            inner,
            text=(
                "Select the local folder where SharePoint syncs your "
                "documents.  This folder should contain the 01_Inbox "
                "directory."
            ),
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            wraplength=_CARD_WIDTH - 100,
            justify="left",
        ).pack(fill="x", pady=(0, PADDING_LG))

        # -- Path label --
        ctk.CTkLabel(
            inner,
            text="SHAREPOINT SYNC FOLDER",
            font=FONT_LABEL,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        # -- Path entry + Browse row --
        path_row = ctk.CTkFrame(inner, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, PADDING_MD))
        path_row.grid_columnconfigure(0, weight=1)
        path_row.grid_columnconfigure(1, weight=0)

        self._path_entry = ctk.CTkEntry(
            path_row,
            placeholder_text="C:\\Users\\...\\SharePoint\\...",
            font=FONT_BODY,
            fg_color=INPUT_BG,
            border_color=INPUT_BORDER,
            text_color=TEXT_PRIMARY,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
        )
        self._path_entry.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            path_row,
            text="Browse...",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_INPUT_HEIGHT,
            width=_BROWSE_BTN_WIDTH,
            corner_radius=CORNER_RADIUS,
            command=self._browse,
        ).grid(row=0, column=1, sticky="e", padx=(PADDING_SM, 0))

        # -- Error label (hidden by default) --
        self._error_label = ctk.CTkLabel(
            inner,
            text="",
            font=FONT_SMALL,
            text_color=ERROR_TEXT,
            wraplength=_CARD_WIDTH - 100,
        )
        self._error_label.pack(fill="x")
        self._error_label.pack_forget()

        # -- Success label / BU list (hidden by default) --
        self._success_label = ctk.CTkLabel(
            inner,
            text="",
            font=FONT_SMALL,
            text_color=SUCCESS_TEXT,
            wraplength=_CARD_WIDTH - 100,
            justify="left",
            anchor="w",
        )
        self._success_label.pack(fill="x")
        self._success_label.pack_forget()

        # -- Confirm button (disabled until valid path) --
        self._confirm_btn = ctk.CTkButton(
            inner,
            text="Confirm  \u2713",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_BUTTON_HEIGHT,
            corner_radius=CORNER_RADIUS,
            state="disabled",
            command=self._handle_confirm,
        )
        self._confirm_btn.pack(fill="x", pady=(PADDING_MD, PADDING_SM))

        # -- Skip link --
        ctk.CTkButton(
            inner,
            text="Skip for now",
            font=FONT_SMALL,
            fg_color="transparent",
            hover_color="#f0f0f0",
            text_color=TEXT_SECONDARY,
            height=28,
            corner_radius=CORNER_RADIUS,
            command=self._on_skip,
        ).pack(pady=(0, PADDING_SM))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        """Open a native folder picker and validate the selected path."""
        folder = filedialog.askdirectory(
            title="Select your SharePoint sync folder",
            mustexist=True,
        )
        if not folder:
            return

        if self._path_entry is not None:
            self._path_entry.delete(0, "end")
            self._path_entry.insert(0, folder)

        self._clear_messages()
        self._validate_path(folder)

    def _validate_path(self, path: str) -> None:
        """Validate the path in a background thread, update UI with results."""
        def _do_validate() -> None:
            try:
                resolved = self._path_discovery.resolve_from_explicit_root(path)
                if self.winfo_exists():
                    self.after(0, self._show_validation_success, resolved)
            except FileNotFoundError as exc:
                if self.winfo_exists():
                    self.after(0, self._show_error, str(exc))

        threading.Thread(target=_do_validate, daemon=True).start()

    def _handle_confirm(self) -> None:
        """Save the path and notify the app shell."""
        path = self._path_entry.get().strip() if self._path_entry else ""
        if not path:
            self._show_error("Please select a folder first.")
            return

        if self._confirm_btn is not None and self._confirm_btn.winfo_exists():
            self._confirm_btn.configure(text="Saving...", state="disabled")

        def _do_confirm() -> None:
            try:
                resolved = self._path_discovery.resolve_from_explicit_root(path)
                self._app_settings.set_sharepoint_root(path)
                if self.winfo_exists():
                    self.after(0, self._on_path_configured, resolved)
            except FileNotFoundError as exc:
                if self.winfo_exists():
                    self.after(0, self._on_confirm_error, str(exc))

        threading.Thread(target=_do_confirm, daemon=True).start()

    # ------------------------------------------------------------------
    # UI feedback helpers
    # ------------------------------------------------------------------

    def _show_validation_success(self, resolved: ResolvedPaths) -> None:
        """Show inbox validation success and enable the confirm button."""
        if self._success_label is not None and self._success_label.winfo_exists():
            self._success_label.configure(
                text="\u2713  Inbox found.  Path validated successfully.",
            )
            self._success_label.pack(fill="x", pady=(0, PADDING_SM))

        if self._confirm_btn is not None and self._confirm_btn.winfo_exists():
            self._confirm_btn.configure(state="normal")

    def _show_error(self, message: str) -> None:
        """Display a red error message."""
        if self._error_label is not None and self._error_label.winfo_exists():
            self._error_label.configure(text=message)
            self._error_label.pack(fill="x", pady=(0, PADDING_SM))

        if self._confirm_btn is not None and self._confirm_btn.winfo_exists():
            self._confirm_btn.configure(state="disabled")

    def _on_confirm_error(self, message: str) -> None:
        """Handle confirm failure — reset button and show error."""
        self._show_error(message)
        if self._confirm_btn is not None and self._confirm_btn.winfo_exists():
            self._confirm_btn.configure(text="Confirm  \u2713", state="disabled")

    def _clear_messages(self) -> None:
        """Hide both error and success labels."""
        if self._error_label is not None and self._error_label.winfo_exists():
            self._error_label.configure(text="")
            self._error_label.pack_forget()
        if self._success_label is not None and self._success_label.winfo_exists():
            self._success_label.configure(text="")
            self._success_label.pack_forget()
        if self._confirm_btn is not None and self._confirm_btn.winfo_exists():
            self._confirm_btn.configure(state="disabled")
