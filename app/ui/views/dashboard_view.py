"""Dashboard View — default landing page after login.

This is the first module stub (``'gatekeeper'``).  In Phase 3 it will
be replaced with the full Card Engine UI.  For Phase 1 it serves as
proof-of-concept for the module system.

**Thin UI Rule**: Zero business logic — only reads and displays.
"""

from __future__ import annotations

from typing import Optional

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
    PADDING_LG,
    PADDING_MD,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_REFRESH_INTERVAL_MS: int = 30_000  # 30 seconds


class DashboardView(ctk.CTkFrame):
    """Placeholder dashboard shown after login.

    Displays:
    - Welcome message with username and role
    - Connectivity status (online / offline)
    - Pending sync-queue count

    The dashboard refreshes its dynamic data every 30 seconds.  The
    refresh timer is cancelled automatically when the widget is destroyed
    to prevent callbacks on a dead widget.

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
        self._refresh_job: Optional[str] = None

        # Dynamic label references (populated by _build_ui)
        self._user_label: Optional[ctk.CTkLabel] = None
        self._role_label: Optional[ctk.CTkLabel] = None
        self._mode_label: Optional[ctk.CTkLabel] = None
        self._pending_label: Optional[ctk.CTkLabel] = None

        self._build_ui()
        self._schedule_refresh()

    # ------------------------------------------------------------------
    # Widget creation
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create all widgets and bind initial data."""
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
        self._user_label = ctk.CTkLabel(
            card,
            text=f"Welcome, {user.full_name}",
            font=FONT_HEADING,
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self._user_label.pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, 4))

        # Role + mode
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

        self._role_label = ctk.CTkLabel(
            info_frame,
            text=f"Role: {user.role}",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self._role_label.pack(side="left")

        mode_text, mode_colour = self._get_mode_display()
        self._mode_label = ctk.CTkLabel(
            info_frame,
            text=f"  \u2022  {mode_text}",
            font=FONT_BODY,
            text_color=mode_colour,
            anchor="w",
        )
        self._mode_label.pack(side="left")

        # Pending sync
        pending = self._db.get_pending_sync_count()
        self._pending_label = ctk.CTkLabel(
            card,
            text=f"Pending sync items: {pending}",
            font=FONT_BODY,
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self._pending_label.pack(fill="x", padx=PADDING_MD, pady=(0, PADDING_MD))

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        """Schedule the next periodic refresh."""
        self._refresh_job = self.after(_REFRESH_INTERVAL_MS, self._refresh)

    def _refresh(self) -> None:
        """Update dynamic label text with fresh data.

        Handles edge cases: if the widget has been destroyed or the
        database is temporarily unavailable, the refresh degrades
        gracefully and reschedules itself.
        """
        try:
            if not self.winfo_exists():
                return

            user = self._session.get_current_user()
            if self._user_label is not None:
                self._user_label.configure(text=f"Welcome, {user.full_name}")
            if self._role_label is not None:
                self._role_label.configure(text=f"Role: {user.role}")

            mode_text, mode_colour = self._get_mode_display()
            if self._mode_label is not None:
                self._mode_label.configure(
                    text=f"  \u2022  {mode_text}",
                    text_color=mode_colour,
                )

            pending = self._db.get_pending_sync_count()
            if self._pending_label is not None:
                self._pending_label.configure(
                    text=f"Pending sync items: {pending}",
                )
        except Exception as exc:
            self._logger.warning(
                "Dashboard refresh failed (non-fatal): %s", exc,
            )

        # Reschedule regardless of success/failure
        if self.winfo_exists():
            self._schedule_refresh()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        """Cancel the pending refresh timer before destroying the widget."""
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        super().destroy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_mode_display(self) -> tuple[str, str]:
        """Return ``(display_text, colour)`` for the connectivity indicator."""
        if self._db.is_online:
            return "Online", STATUS_ONLINE
        return "Offline", STATUS_OFFLINE
