"""Settings View — SharePoint path configuration module.

Sidebar module allowing users to view and reconfigure the monitored
SharePoint sync folder path.  Displays current path, watcher status,
and detected business-unit subfolders.

**Thin UI Rule**: All validation and persistence is delegated to
``PathDiscoveryService`` and ``AppSettingsService``.
"""

from __future__ import annotations

import threading
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk

from app.logger import StructuredLogger
from app.models.file_models import ResolvedPaths
from app.services.app_settings_service import AppSettingsService
from app.services.file_watcher import FileWatcherService
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
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_SYNCING,
    SUCCESS_TEXT,
    TEXT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_INPUT_HEIGHT: int = 44
_BROWSE_BTN_WIDTH: int = 100


class SettingsView(ctk.CTkFrame):
    """Settings module for SharePoint path configuration.

    Parameters
    ----------
    parent:
        Content container provided by the Host Shell.
    app_settings:
        Service for reading/writing persistent settings.
    path_discovery:
        Service for validating SharePoint folder structure.
    file_watcher:
        File watcher service instance (may be ``None``).
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        app_settings: AppSettingsService,
        path_discovery: PathDiscoveryService,
        file_watcher: Optional[FileWatcherService],
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)
        self._app_settings = app_settings
        self._path_discovery = path_discovery
        self._file_watcher = file_watcher
        self._logger = logger

        # Widget references
        self._path_value_label: Optional[ctk.CTkLabel] = None
        self._status_dot: Optional[ctk.CTkLabel] = None
        self._status_text: Optional[ctk.CTkLabel] = None
        self._bu_label: Optional[ctk.CTkLabel] = None
        self._message_label: Optional[ctk.CTkLabel] = None
        self._path_entry: Optional[ctk.CTkEntry] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Widget creation
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the settings interface."""
        stored_path = self._app_settings.get_sharepoint_root() or ""

        # --- Heading ---
        ctk.CTkLabel(
            self,
            text="Settings",
            font=FONT_HEADING,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=PADDING_LG, pady=(PADDING_LG, PADDING_MD))

        # --- SharePoint Path Card ---
        card = ctk.CTkFrame(
            self,
            fg_color=CONTENT_CARD_BG,
            corner_radius=CORNER_RADIUS,
        )
        card.pack(fill="x", padx=PADDING_LG, pady=(0, PADDING_MD))

        ctk.CTkLabel(
            card,
            text="SharePoint Sync Folder",
            font=FONT_LABEL,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, PADDING_SM))

        # Current path display
        self._path_value_label = ctk.CTkLabel(
            card,
            text=stored_path or "Not configured",
            font=FONT_BODY,
            text_color=TEXT_PRIMARY if stored_path else TEXT_SECONDARY,
            anchor="w",
            wraplength=600,
        )
        self._path_value_label.pack(
            fill="x", padx=PADDING_MD, pady=(0, PADDING_SM),
        )

        # Watcher status row
        status_row = ctk.CTkFrame(card, fg_color="transparent")
        status_row.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_SM))

        watcher_running = (
            self._file_watcher is not None
            and self._file_watcher.is_running
        )

        if self._file_watcher is None:
            dot_color = STATUS_OFFLINE
            status_text = "File watcher not configured"
        elif watcher_running:
            dot_color = STATUS_ONLINE
            status_text = "File watcher active"
        else:
            dot_color = STATUS_SYNCING
            status_text = "File watcher stopped"

        self._status_dot = ctk.CTkLabel(
            status_row,
            text="\u25CF",
            font=FONT_SMALL,
            text_color=dot_color,
            width=20,
        )
        self._status_dot.pack(side="left")

        self._status_text = ctk.CTkLabel(
            status_row,
            text=status_text,
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
        )
        self._status_text.pack(side="left", padx=(2, 0))

        # Business units (try to validate current path)
        self._bu_label = ctk.CTkLabel(
            card,
            text="",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
            anchor="w",
            wraplength=600,
        )
        self._bu_label.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

        if stored_path:
            self._load_bu_info(stored_path)

        # --- Change Path Card ---
        change_card = ctk.CTkFrame(
            self,
            fg_color=CONTENT_CARD_BG,
            corner_radius=CORNER_RADIUS,
        )
        change_card.pack(fill="x", padx=PADDING_LG, pady=(0, PADDING_MD))

        ctk.CTkLabel(
            change_card,
            text="Change Monitored Folder",
            font=FONT_LABEL,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, PADDING_SM))

        # Path entry + browse
        path_row = ctk.CTkFrame(change_card, fg_color="transparent")
        path_row.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_SM))
        path_row.grid_columnconfigure(0, weight=1)
        path_row.grid_columnconfigure(1, weight=0)

        self._path_entry = ctk.CTkEntry(
            path_row,
            placeholder_text="Browse to select a new folder...",
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

        # Save button
        ctk.CTkButton(
            change_card,
            text="Save Path",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            height=_INPUT_HEIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._save_path,
        ).pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

        # Message label (success / error)
        self._message_label = ctk.CTkLabel(
            change_card,
            text="",
            font=FONT_SMALL,
            text_color=SUCCESS_TEXT,
            wraplength=600,
            anchor="w",
        )
        self._message_label.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))
        self._message_label.pack_forget()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        """Open a native folder picker."""
        folder = filedialog.askdirectory(
            title="Select your SharePoint sync folder",
            mustexist=True,
        )
        if folder and self._path_entry is not None:
            self._path_entry.delete(0, "end")
            self._path_entry.insert(0, folder)

    def _save_path(self) -> None:
        """Validate and save the new path."""
        path = self._path_entry.get().strip() if self._path_entry else ""
        if not path:
            self._show_message("Please select a folder first.", error=True)
            return

        def _do_save() -> None:
            try:
                self._path_discovery.resolve_from_explicit_root(path)
                self._app_settings.set_sharepoint_root(path)
                if self.winfo_exists():
                    self.after(0, self._on_save_success, path)
            except FileNotFoundError as exc:
                if self.winfo_exists():
                    self.after(0, self._show_message, str(exc), True)

        threading.Thread(target=_do_save, daemon=True).start()

    def _load_bu_info(self, path: str) -> None:
        """Load and display BU subfolder info for the given path."""
        def _do_load() -> None:
            try:
                resolved = self._path_discovery.resolve_from_explicit_root(path)
                text = "Path validated successfully."
                if self.winfo_exists():
                    self.after(0, self._update_bu_label, text)
            except FileNotFoundError:
                if self.winfo_exists():
                    self.after(
                        0,
                        self._update_bu_label,
                        "Stored path no longer valid.",
                    )

        threading.Thread(target=_do_load, daemon=True).start()

    # ------------------------------------------------------------------
    # UI feedback helpers
    # ------------------------------------------------------------------

    def _on_save_success(self, path: str) -> None:
        """Update display after successful save."""
        if self._path_value_label is not None and self._path_value_label.winfo_exists():
            self._path_value_label.configure(text=path, text_color=TEXT_PRIMARY)

        self._load_bu_info(path)
        self._show_message(
            "Path saved.  Log out and back in to activate the new path.",
            error=False,
        )

    def _update_bu_label(self, text: str) -> None:
        """Set the BU label text."""
        if self._bu_label is not None and self._bu_label.winfo_exists():
            self._bu_label.configure(text=text)

    def _show_message(self, text: str, error: bool = False) -> None:
        """Show a success or error message below the save button."""
        if self._message_label is not None and self._message_label.winfo_exists():
            self._message_label.configure(
                text=text,
                text_color=ERROR_TEXT if error else SUCCESS_TEXT,
            )
            self._message_label.pack(
                fill="x", padx=PADDING_MD, pady=(0, PADDING_MD),
            )
