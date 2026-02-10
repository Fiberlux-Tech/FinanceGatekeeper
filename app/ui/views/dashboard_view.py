"""Dashboard View — default landing page after login.

This is the first module stub (``'gatekeeper'``).  In Phase 3 it will
be replaced with the full Card Engine UI.  For Phase 1 it serves as
proof-of-concept for the module system.

**Thin UI Rule**: Zero business logic — only reads and displays.
"""

from __future__ import annotations

import customtkinter as ctk

from app.auth import SessionManager
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.ui.theme import (
    CONTENT_BG,
    CONTENT_CARD_BG,
    CORNER_RADIUS,
    FONT_BODY,
    FONT_HEADING,
    FONT_SMALL,
    PADDING_LG,
    PADDING_MD,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class DashboardView(ctk.CTkFrame):
    """Placeholder dashboard shown after login.

    Displays:
    - Welcome message with username and role
    - Connectivity status (online / offline)
    - Pending sync-queue count

    Parameters
    ----------
    parent:
        Content container provided by the Host Shell.
    session:
        Used to read the current user's identity.
    db:
        Used to check ``is_online`` and query sync queue.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        session: SessionManager,
        db: DatabaseManager,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, fg_color=CONTENT_BG)
        self._session = session
        self._db = db
        self._logger = logger
        self._build_ui()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        user = self._session.get_current_user()

        # --- Card container ---
        card = ctk.CTkFrame(
            self,
            fg_color=CONTENT_CARD_BG,
            corner_radius=CORNER_RADIUS,
        )
        card.pack(
            padx=PADDING_LG,
            pady=PADDING_LG,
            fill="x",
        )

        # Welcome
        ctk.CTkLabel(
            card,
            text=f"Welcome, {user.full_name}",
            font=FONT_HEADING,
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, 4))

        # Role + mode
        mode_text = "Online" if self._db.is_online else "Offline"
        mode_colour = STATUS_ONLINE if self._db.is_online else STATUS_OFFLINE
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

        ctk.CTkLabel(
            info_frame,
            text=f"Role: {user.role}",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            info_frame,
            text=f"  \u2022  {mode_text}",
            font=FONT_BODY,
            text_color=mode_colour,
            anchor="w",
        ).pack(side="left")

        # Pending sync
        pending = self._get_pending_sync_count()
        ctk.CTkLabel(
            card,
            text=f"Pending sync items: {pending}",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

        # Phase 3 placeholder
        ctk.CTkLabel(
            self,
            text="The Gatekeeper Card Engine will be built in Phase 3.",
            font=FONT_SMALL,
            text_color=TEXT_SECONDARY,
        ).pack(pady=PADDING_LG)

    def _get_pending_sync_count(self) -> int:
        """Return the number of pending items in the sync queue."""
        return self._db.get_pending_sync_count()
