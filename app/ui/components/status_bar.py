"""Status Bar Component.

Bottom bar showing connectivity status, pending sync count, and
application version.  Auto-refreshes every 30 seconds.

**Thin UI Rule**: No business logic — only reads ``db.is_online`` and
a ``COUNT(*)`` query on ``sync_queue``.
"""

from __future__ import annotations

import customtkinter as ctk

import app as _app_pkg
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.ui.theme import (
    FONT_SMALL,
    PADDING_SM,
    SIDEBAR_BG,
    STATUS_BAR_HEIGHT,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_SYNCING,
    TEXT_LIGHT,
)

_REFRESH_INTERVAL_MS: int = 30_000  # 30 seconds


class StatusBar(ctk.CTkFrame):
    """Application-wide status bar at the bottom of the Host Shell.

    Displays:
    - A coloured dot indicating connectivity (green = online,
      red = offline, yellow = pending sync items).
    - The number of pending sync-queue items.
    - The application version from ``app.__version__``.

    The bar refreshes automatically every 30 seconds via
    ``self.after()``.

    Parameters
    ----------
    parent:
        Parent widget (typically the AppShell root).
    db:
        Used to check ``is_online`` and query the sync queue.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        db: DatabaseManager,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(
            parent,
            height=STATUS_BAR_HEIGHT,
            fg_color=SIDEBAR_BG,
        )
        self.pack_propagate(False)

        self._db = db
        self._logger = logger

        # --- Widgets ---
        self._status_dot = ctk.CTkLabel(
            self,
            text="\u25CF",
            font=FONT_SMALL,
            text_color=STATUS_ONLINE,
            width=20,
        )
        self._status_dot.pack(side="left", padx=(PADDING_SM, 2))

        self._status_label = ctk.CTkLabel(
            self,
            text="",
            font=FONT_SMALL,
            text_color=TEXT_LIGHT,
            anchor="w",
        )
        self._status_label.pack(side="left", padx=(0, PADDING_SM))

        self._version_label = ctk.CTkLabel(
            self,
            text=f"v{_app_pkg.__version__}",
            font=FONT_SMALL,
            text_color=TEXT_LIGHT,
            anchor="e",
        )
        self._version_label.pack(side="right", padx=PADDING_SM)

        # Initial update + periodic refresh
        self.update_status()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update_status(self) -> None:
        """Refresh the connectivity indicator and pending sync count."""
        pending = self._get_pending_count()
        is_online = self._db.is_online

        if is_online and pending == 0:
            colour = STATUS_ONLINE
            text = "Online"
        elif is_online and pending > 0:
            colour = STATUS_SYNCING
            text = f"Syncing ({pending} pending)"
        else:
            colour = STATUS_OFFLINE
            text = f"Offline ({pending} pending)" if pending else "Offline"

        self._status_dot.configure(text_color=colour)
        self._status_label.configure(text=text)

        # Schedule next refresh
        self.after(_REFRESH_INTERVAL_MS, self.update_status)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_pending_count(self) -> int:
        """Count pending items in the sync queue."""
        return self._db.get_pending_sync_count()
