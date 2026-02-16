"""
Detail Panel Component — Right Side of Master-Detail Split.

Displays full file information when a card is selected in the
master list.  Sections include:

1. Header — client name, clickable filename badge, submitted-by line
2. Transaction Overview — BU, RUC/DNI, client, contract term, MRC, NRC
3. Discrepancy Alert — conditional warning box if parse_error exists
4. Chain of Custody — SHA-256 hash, file status
5. Actions — Open in Excel, Open Folder, Refresh

All data displayed is backed by real ``CardData`` fields.  No
placeholder values or mock data.

**Thin UI Rule**: Zero business logic — only display and callbacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from app.models.card_models import CardData
from app.models.enums import FileStatus
from app.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRIMARY,
    CONTENT_BG,
    CONTENT_CARD_BG,
    CORNER_RADIUS,
    FONT_BODY,
    FONT_BUTTON,
    FONT_CAPTION,
    FONT_HEADING,
    FONT_LABEL,
    FONT_SMALL,
    PADDING_MD,
    PADDING_SM,
    STATUS_OFFLINE,
    STATUS_OFFLINE_HOVER,
    STATUS_ONLINE,
    STATUS_ONLINE_HOVER,
    STATUS_SYNCING,
    TEXT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_BG,
    WARNING_BORDER,
    WARNING_TEXT,
)

_SECTION_TITLE_FONT = FONT_LABEL
_HASH_FONT = (FONT_CAPTION[0], FONT_CAPTION[1])


class DetailPanel(ctk.CTkScrollableFrame):
    """Full-information panel for the selected inbox file.

    Parameters
    ----------
    parent:
        The right-side container frame.
    on_open_file:
        Callback ``(path) -> None`` to open the file in Excel.
    on_open_folder:
        Callback ``(path) -> None`` to open the containing folder.
    on_refresh:
        Callback ``(path) -> None`` to re-scan a single file.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        on_open_file: Callable[[Path], None],
        on_open_folder: Callable[[Path], None],
        on_refresh: Callable[[Path], None],
        on_approve: Callable[["CardData"], None],
        on_reject: Callable[["CardData"], None],
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)
        self._on_open_file = on_open_file
        self._on_open_folder = on_open_folder
        self._on_refresh = on_refresh
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._current_path: Optional[Path] = None
        self._current_card_data: Optional[CardData] = None

        # Widget references (populated by _build_detail or _build_empty)
        self._detail_frame: Optional[ctk.CTkFrame] = None
        self._empty_frame: Optional[ctk.CTkFrame] = None

        self.show_empty()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_card(self, data: CardData) -> None:
        """Populate the panel with full details from *data*."""
        self._current_path = data.path
        self._current_card_data = data
        self._clear()
        self._build_detail(data)

    def show_empty(self) -> None:
        """Show the empty state placeholder."""
        self._current_path = None
        self._current_card_data = None
        self._clear()
        self._build_empty()

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_empty(self) -> None:
        """Create the "no selection" placeholder."""
        self._empty_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._empty_frame.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self._empty_frame,
            text="Select a file to view details",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
        ).place(relx=0.5, rely=0.4, anchor="center")

    def _build_detail(self, data: CardData) -> None:
        """Build the full detail layout for *data*."""
        self._detail_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._detail_frame.pack(fill="both", expand=True, padx=PADDING_MD, pady=PADDING_SM)

        # --- Section 1: Header bar ---
        self._build_header(data)

        # --- Section 2: Transaction Overview ---
        self._build_transaction_overview(data)

        # --- Section 3: Discrepancy alert (conditional) ---
        self._build_discrepancy_alert(data)

        # --- Section 4: Chain of Custody ---
        self._build_custody_section(data)

        # --- Section 5: Actions ---
        self._build_actions(data)

        # --- Section 6: Decision (Approve / Reject) ---
        self._build_approval_actions(data)

    def _build_header(self, data: CardData) -> None:
        """Client name, clickable filename badge, and submitted-by subtitle."""
        assert self._detail_frame is not None

        header = ctk.CTkFrame(self._detail_frame, fg_color="transparent")
        header.pack(fill="x", pady=(0, PADDING_MD))

        # Client name
        ctk.CTkLabel(
            header,
            text=data.client_name or "Unknown Client",
            font=FONT_HEADING,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x")

        # Filename badge row (file icon + clickable filename)
        badge_row = ctk.CTkFrame(header, fg_color="transparent")
        badge_row.pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(
            badge_row, text="\U0001F4C4",
            font=FONT_SMALL, text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 4))

        filename_label = ctk.CTkLabel(
            badge_row,
            text=data.filename,
            font=FONT_SMALL,
            text_color=ACCENT_PRIMARY,
            anchor="w",
            cursor="hand2",
        )
        filename_label.pack(side="left")
        filename_label.bind(
            "<Button-1>", lambda _: self._on_open_file(data.path),
        )

        # Submitted-by line
        submitted_by = data.salesman or "Unknown"
        date_str = data.modified_at.strftime("%m/%d/%Y")
        ctk.CTkLabel(
            header,
            text=f"Submitted by {submitted_by} \u2022 {date_str}",
            font=FONT_CAPTION,
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

    def _build_transaction_overview(self, data: CardData) -> None:
        """Transaction data in a white card with 2-column grid."""
        assert self._detail_frame is not None

        card = self._make_section_card(self._detail_frame, "Transaction Overview")

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=PADDING_SM, pady=(0, PADDING_SM))
        grid.columnconfigure((0, 1), weight=1)

        self._add_kv(
            grid, "UNIDAD DE NEGOCIO",
            data.business_unit.value if data.business_unit else "\u2014",
            0, 0,
        )
        self._add_kv(
            grid, "RUC/DNI",
            str(data.company_id) if data.company_id else "\u2014",
            0, 1,
        )
        self._add_kv(
            grid, "NOMBRE CLIENTE",
            data.client_name or "\u2014",
            1, 0,
        )
        self._add_kv(
            grid, "PLAZO DE CONTRATO",
            f"{data.plazo_contrato} meses" if data.plazo_contrato else "\u2014",
            1, 1,
        )
        self._add_kv(
            grid, "MRC (RECURRENTE MENSUAL)",
            _fmt_currency_pen(data.mrc),
            2, 0,
        )
        self._add_kv(
            grid, "NRC (PAGO \u00daNICO)",
            _fmt_currency_pen(data.nrc),
            2, 1,
        )

    def _build_discrepancy_alert(self, data: CardData) -> None:
        """Show amber warning box if parse_error exists."""
        if not data.parse_error:
            return

        assert self._detail_frame is not None

        alert = ctk.CTkFrame(
            self._detail_frame,
            fg_color=WARNING_BG,
            border_width=1,
            border_color=WARNING_BORDER,
            corner_radius=CORNER_RADIUS,
        )
        alert.pack(fill="x", pady=(0, PADDING_SM))

        alert_inner = ctk.CTkFrame(alert, fg_color="transparent")
        alert_inner.pack(fill="x", padx=PADDING_MD, pady=PADDING_SM)

        ctk.CTkLabel(
            alert_inner,
            text="\u26A0  Discrepancy Detected",
            font=FONT_LABEL,
            text_color=WARNING_TEXT,
            anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            alert_inner,
            text=data.parse_error,
            font=FONT_SMALL,
            text_color=WARNING_TEXT,
            anchor="w",
            wraplength=500,
        ).pack(fill="x", pady=(4, 0))

    def _build_custody_section(self, data: CardData) -> None:
        """SHA-256 hash and file status dot."""
        assert self._detail_frame is not None

        card = self._make_section_card(self._detail_frame, "Chain of Custody")

        custody_grid = ctk.CTkFrame(card, fg_color="transparent")
        custody_grid.pack(fill="x", padx=PADDING_SM, pady=(0, PADDING_SM))

        # SHA-256
        hash_row = ctk.CTkFrame(custody_grid, fg_color="transparent")
        hash_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            hash_row, text="SHA-256:",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(side="left")

        hash_text = data.sha256 or "Not computed"
        ctk.CTkLabel(
            hash_row, text=f"  {hash_text}",
            font=_HASH_FONT, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # Status
        status_row = ctk.CTkFrame(custody_grid, fg_color="transparent")
        status_row.pack(fill="x")

        ctk.CTkLabel(
            status_row, text="Status:",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(side="left")

        dot_color = _status_color(data.file_status)
        ctk.CTkLabel(
            status_row, text="  \u25CF",
            font=FONT_SMALL, text_color=dot_color,
        ).pack(side="left")

        ctk.CTkLabel(
            status_row, text=f"  {_status_text(data.file_status)}",
            font=FONT_SMALL, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left")

    def _build_actions(self, data: CardData) -> None:
        """Action buttons: Open in Excel, Open Folder, Refresh."""
        assert self._detail_frame is not None

        actions = ctk.CTkFrame(self._detail_frame, fg_color="transparent")
        actions.pack(fill="x", pady=(PADDING_MD, 0))

        path = data.path

        ctk.CTkButton(
            actions,
            text="Open in Excel",
            font=FONT_BUTTON,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color=TEXT_LIGHT,
            corner_radius=CORNER_RADIUS,
            command=lambda: self._on_open_file(path),
        ).pack(side="left", padx=(0, PADDING_SM))

        ctk.CTkButton(
            actions,
            text="Open Folder",
            font=FONT_BUTTON,
            fg_color="transparent",
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_PRIMARY,
            border_width=1,
            border_color=ACCENT_PRIMARY,
            corner_radius=CORNER_RADIUS,
            command=lambda: self._on_open_folder(path),
        ).pack(side="left", padx=(0, PADDING_SM))

        ctk.CTkButton(
            actions,
            text="Refresh",
            font=FONT_BUTTON,
            fg_color="transparent",
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_PRIMARY,
            border_width=1,
            border_color=ACCENT_PRIMARY,
            corner_radius=CORNER_RADIUS,
            command=lambda: self._on_refresh(path),
        ).pack(side="left")

    def _build_approval_actions(self, data: CardData) -> None:
        """Approve / Reject buttons — shown only for parsed, ready files."""
        assert self._detail_frame is not None

        if not data.is_parsed or data.file_status != FileStatus.READY:
            return

        card = self._make_section_card(self._detail_frame, "Decision")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=PADDING_SM, pady=(0, PADDING_SM))

        ctk.CTkButton(
            btn_row,
            text="Approve",
            font=FONT_BUTTON,
            fg_color=STATUS_ONLINE,
            hover_color=STATUS_ONLINE_HOVER,
            text_color=TEXT_LIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._handle_approve,
        ).pack(side="left", padx=(0, PADDING_SM))

        ctk.CTkButton(
            btn_row,
            text="Reject",
            font=FONT_BUTTON,
            fg_color=STATUS_OFFLINE,
            hover_color=STATUS_OFFLINE_HOVER,
            text_color=TEXT_LIGHT,
            corner_radius=CORNER_RADIUS,
            command=self._handle_reject,
        ).pack(side="left")

    def _handle_approve(self) -> None:
        """Forward the stored card data to the approve callback."""
        if self._current_card_data is not None:
            self._on_approve(self._current_card_data)

    def _handle_reject(self) -> None:
        """Forward the stored card data to the reject callback."""
        if self._current_card_data is not None:
            self._on_reject(self._current_card_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_section_card(
        self, parent: ctk.CTkFrame, title: str,
    ) -> ctk.CTkFrame:
        """Create a white rounded section card with a title label."""
        card = ctk.CTkFrame(
            parent, fg_color=CONTENT_CARD_BG, corner_radius=CORNER_RADIUS,
        )
        card.pack(fill="x", pady=(0, PADDING_SM))

        ctk.CTkLabel(
            card, text=title,
            font=_SECTION_TITLE_FONT, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", padx=PADDING_SM, pady=(PADDING_SM, 4))

        return card

    @staticmethod
    def _add_kv(
        grid: ctk.CTkFrame,
        label: str,
        value: str,
        row: int,
        col: int,
    ) -> None:
        """Add a key-value pair to a grid at (row, col)."""
        cell = ctk.CTkFrame(grid, fg_color="transparent")
        cell.grid(row=row, column=col, sticky="w", padx=(0, PADDING_MD), pady=2)

        ctk.CTkLabel(
            cell, text=label,
            font=FONT_CAPTION, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            cell, text=value,
            font=FONT_SMALL, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x")

    def _clear(self) -> None:
        """Destroy all content frames."""
        if self._detail_frame is not None:
            self._detail_frame.destroy()
            self._detail_frame = None
        if self._empty_frame is not None:
            self._empty_frame.destroy()
            self._empty_frame = None


# ======================================================================
# Module-level display helpers
# ======================================================================


def _fmt_currency_pen(value: Optional[float]) -> str:
    """Format a float as PEN currency or em-dash."""
    if value is None:
        return "\u2014"
    return f"{value:,.2f} PEN"


def _status_color(status: FileStatus) -> str:
    """Return the theme colour for a file status."""
    if status == FileStatus.READY:
        return STATUS_ONLINE
    if status == FileStatus.LOCKED:
        return STATUS_OFFLINE
    return STATUS_SYNCING


def _status_text(status: FileStatus) -> str:
    """Return a short display label for a file status."""
    if status == FileStatus.LOCKED:
        return "Locked"
    if status == FileStatus.SYNCING:
        return "Syncing"
    return "Ready"
