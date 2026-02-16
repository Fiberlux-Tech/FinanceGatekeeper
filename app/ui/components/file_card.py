"""
File Card Component — Master List Entry.

Compact card for the Card Engine's left-panel master list.
Each card displays one inbox file with headline metadata extracted
from the Excel template:

- Client name + optional warning icon
- Sales rep
- MRC value + payback (contract term)

Selected state uses a purple left accent bar.

**Thin UI Rule**: Zero business logic — only display and callbacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from app.models.card_models import CardData
from app.ui.theme import (
    ACCENT_PRIMARY,
    CARD_SELECTED_BG,
    CARD_WARNING_COLOR,
    CONTENT_CARD_BG,
    CORNER_RADIUS,
    FONT_BODY,
    FONT_CAPTION,
    FONT_LABEL,
    FONT_SMALL,
    PADDING_MD,
    PADDING_SM,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class FileCard(ctk.CTkFrame):
    """Compact card representing one inbox file in the master list.

    Parameters
    ----------
    parent:
        Scrollable container frame.
    card_data:
        Enriched file data to display.
    on_select:
        Callback invoked with the file path when the card is clicked.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        card_data: CardData,
        on_select: Callable[[Path], None],
    ) -> None:
        super().__init__(
            parent,
            fg_color=CONTENT_CARD_BG,
            corner_radius=CORNER_RADIUS,
            cursor="hand2",
        )
        self._on_select = on_select
        self._card_data = card_data
        self._is_selected: bool = False

        self._build_ui(card_data)
        self._bind_click_recursive(self)

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_ui(self, data: CardData) -> None:
        """Create the 3-row compact card layout."""
        # Left accent bar (selection indicator)
        self._accent_bar = ctk.CTkFrame(
            self, width=4, fg_color="transparent", corner_radius=0,
        )
        self._accent_bar.pack(side="left", fill="y")
        self._accent_bar.pack_propagate(False)

        # Content area
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", expand=True, padx=(PADDING_SM, PADDING_MD), pady=PADDING_SM)

        # --- Row 1: Client name + warning icon ---
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 4))

        self._lbl_client = ctk.CTkLabel(
            row1,
            text=data.client_name or "Unknown Client",
            font=FONT_LABEL,
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self._lbl_client.pack(side="left", fill="x", expand=True)

        # Warning icon — only packed if parse_error exists
        self._warning_icon = ctk.CTkLabel(
            row1,
            text="\u26A0",
            font=FONT_BODY,
            text_color=CARD_WARNING_COLOR,
            width=20,
        )
        if data.parse_error:
            self._warning_icon.pack(side="right", padx=(4, 0))

        # --- Row 2: Person icon + salesman ---
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            row2,
            text="\U0001F464",
            font=FONT_CAPTION,
            text_color=TEXT_SECONDARY,
            width=16,
        ).pack(side="left", padx=(0, 4))

        self._lbl_salesman = ctk.CTkLabel(
            row2,
            text=data.salesman or "\u2014",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self._lbl_salesman.pack(side="left")

        # --- Row 3: MRC + Payback ---
        row3 = ctk.CTkFrame(inner, fg_color="transparent")
        row3.pack(fill="x")

        # MRC
        ctk.CTkLabel(
            row3, text="$ MRC",
            font=FONT_CAPTION, text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 4))

        self._lbl_mrc = ctk.CTkLabel(
            row3,
            text=_format_currency(data.mrc),
            font=FONT_LABEL, text_color=TEXT_PRIMARY,
        )
        self._lbl_mrc.pack(side="left", padx=(0, PADDING_MD))

        # Separator dot
        ctk.CTkLabel(
            row3, text="\u25CF",
            font=(FONT_CAPTION[0], 6), text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(0, PADDING_SM))

        # Payback
        ctk.CTkLabel(
            row3, text="PAYBACK",
            font=FONT_CAPTION, text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 4))

        self._lbl_payback = ctk.CTkLabel(
            row3,
            text=_format_payback(data.plazo_contrato),
            font=FONT_LABEL, text_color=TEXT_PRIMARY,
        )
        self._lbl_payback.pack(side="left")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_data(self, card_data: CardData) -> None:
        """Update all label texts with fresh ``CardData``.

        Called when the user refreshes a file or a watchdog MODIFIED
        event fires.  Avoids rebuilding the entire widget tree.
        """
        self._card_data = card_data
        self._lbl_client.configure(
            text=card_data.client_name or "Unknown Client",
        )
        self._lbl_salesman.configure(text=card_data.salesman or "\u2014")
        self._lbl_mrc.configure(text=_format_currency(card_data.mrc))
        self._lbl_payback.configure(text=_format_payback(card_data.plazo_contrato))

        # Show/hide warning icon based on parse_error
        if card_data.parse_error:
            if not self._warning_icon.winfo_ismapped():
                self._warning_icon.pack(side="right", padx=(4, 0))
        else:
            self._warning_icon.pack_forget()

    def set_selected(self, selected: bool) -> None:
        """Toggle the visual selected state."""
        self._is_selected = selected
        if selected:
            self._accent_bar.configure(fg_color=ACCENT_PRIMARY)
            self.configure(fg_color=CARD_SELECTED_BG)
        else:
            self._accent_bar.configure(fg_color="transparent")
            self.configure(fg_color=CONTENT_CARD_BG)

    @property
    def card_data(self) -> CardData:
        """Return the current ``CardData`` for this card."""
        return self._card_data

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------

    def _bind_click_recursive(self, widget: ctk.CTkBaseClass) -> None:
        """Bind left-click to every child widget so the whole card is clickable."""
        widget.bind("<Button-1>", self._on_click)
        for child in widget.winfo_children():
            self._bind_click_recursive(child)

    def _on_click(self, _event: object) -> None:
        """Dispatch selection callback."""
        self._on_select(self._card_data.path)


# ======================================================================
# Module-level display helpers
# ======================================================================


def _format_currency(value: Optional[float]) -> str:
    """Format a float as currency (e.g. ``$12,500``)."""
    if value is None:
        return "\u2014"
    return f"${value:,.2f}"


def _format_payback(plazo_contrato: Optional[int]) -> str:
    """Format the contract term as a payback label."""
    if plazo_contrato is None:
        return "\u2014"
    return f"{plazo_contrato:.1f} mo"
