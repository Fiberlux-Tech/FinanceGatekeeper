"""Sidebar Navigation Component.

Displays the list of registered modules, the authenticated user's
identity, and a logout button.  Follows the **Thin UI** rule: zero
business logic — all actions are delegated via injected callbacks.
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from app.auth import SessionManager
from app.logger import StructuredLogger
from app.ui.theme import (
    ACCENT_PRIMARY,
    FONT_BODY,
    FONT_SIDEBAR,
    FONT_SIDEBAR_ACTIVE,
    FONT_SMALL,
    PADDING_MD,
    PADDING_SM,
    SIDEBAR_ACTIVE,
    SIDEBAR_BG,
    SIDEBAR_HOVER,
    SIDEBAR_TEXT,
    SIDEBAR_WIDTH,
    TEXT_LIGHT,
)

_AVATAR_SIZE: int = 40
_LOGOUT_RED: str = "#e74c3c"
_LOGOUT_RED_HOVER: str = "#3a1a1a"


class _ModuleButton(ctk.CTkButton):
    """Internal clickable sidebar entry for a single module."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        module_id: str,
        display_name: str,
        icon: str,
        on_click: Callable[[str], None],
    ) -> None:
        self._module_id = module_id
        super().__init__(
            parent,
            text=f"  {icon}   {display_name}",
            anchor="w",
            font=FONT_SIDEBAR,
            text_color=SIDEBAR_TEXT,
            fg_color="transparent",
            hover_color=SIDEBAR_HOVER,
            height=40,
            corner_radius=6,
            command=lambda: on_click(self._module_id),
        )

    @property
    def module_id(self) -> str:
        return self._module_id

    def set_active(self, active: bool) -> None:
        """Highlight or un-highlight this button."""
        if active:
            self.configure(fg_color=SIDEBAR_ACTIVE, font=FONT_SIDEBAR_ACTIVE)
        else:
            self.configure(fg_color="transparent", font=FONT_SIDEBAR)


class SidebarNav(ctk.CTkFrame):
    """Sidebar navigation panel for the Host Shell.

    Responsibilities (all purely visual):
    - Display the authenticated user's name and role.
    - Render a list of module buttons.
    - Highlight the currently active module.
    - Provide a logout button.

    All actions (module switch, logout) are dispatched through injected
    callbacks — the sidebar performs **no** business logic.

    Parameters
    ----------
    parent:
        The parent widget (typically the AppShell root).
    on_module_selected:
        Called with the ``module_id`` when the user clicks a module.
    on_logout:
        Called when the user clicks the Logout button.
    session:
        Used only to read the current user's display name and role.
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        on_module_selected: Callable[[str], None],
        on_logout: Callable[[], None],
        session: SessionManager,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(parent, width=SIDEBAR_WIDTH, fg_color=SIDEBAR_BG)
        self.pack_propagate(False)

        self._on_module_selected = on_module_selected
        self._on_logout = on_logout
        self._session = session
        self._logger = logger

        self._buttons: dict[str, _ModuleButton] = {}
        self._active_module_id: Optional[str] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_module(
        self,
        module_id: str,
        display_name: str,
        icon: str,
    ) -> None:
        """Add a module entry to the sidebar."""
        btn = _ModuleButton(
            parent=self._modules_frame,
            module_id=module_id,
            display_name=display_name,
            icon=icon,
            on_click=self._on_module_selected,
        )
        btn.pack(fill="x", padx=PADDING_SM, pady=2)
        self._buttons[module_id] = btn

    def set_active(self, module_id: str) -> None:
        """Highlight *module_id* and un-highlight the previous one."""
        if self._active_module_id and self._active_module_id in self._buttons:
            self._buttons[self._active_module_id].set_active(False)
        if module_id in self._buttons:
            self._buttons[module_id].set_active(True)
        self._active_module_id = module_id

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construct the sidebar layout."""
        user = self._session.get_current_user()

        # --- User info section: avatar + name + role ---
        user_frame = ctk.CTkFrame(self, fg_color="transparent")
        user_frame.pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, PADDING_SM))

        # Row: avatar circle | name + role
        row = ctk.CTkFrame(user_frame, fg_color="transparent")
        row.pack(fill="x")

        # Circular avatar with initials
        initials = self._get_initials(user.full_name)
        avatar = ctk.CTkFrame(
            row,
            width=_AVATAR_SIZE,
            height=_AVATAR_SIZE,
            corner_radius=_AVATAR_SIZE // 2,
            fg_color=ACCENT_PRIMARY,
        )
        avatar.pack(side="left", padx=(0, 10))
        avatar.pack_propagate(False)

        ctk.CTkLabel(
            avatar,
            text=initials,
            font=("Segoe UI", 14, "bold"),
            text_color=TEXT_LIGHT,
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Name + role stacked
        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            text_frame,
            text=user.full_name,
            font=FONT_SIDEBAR_ACTIVE,
            text_color=TEXT_LIGHT,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            text_frame,
            text=user.role,
            font=FONT_SMALL,
            text_color=SIDEBAR_TEXT,
            anchor="w",
        ).pack(fill="x")

        # --- Separator ---
        sep = ctk.CTkFrame(self, height=1, fg_color=SIDEBAR_HOVER)
        sep.pack(fill="x", padx=PADDING_MD, pady=PADDING_SM)

        # --- Module list (scrollable area) ---
        self._modules_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._modules_frame.pack(fill="both", expand=True, padx=0, pady=PADDING_SM)

        # --- Bottom section: separator + logout ---
        bottom_sep = ctk.CTkFrame(self, height=1, fg_color=SIDEBAR_HOVER)
        bottom_sep.pack(fill="x", padx=PADDING_MD, side="bottom")

        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(
            fill="x", padx=PADDING_SM, pady=PADDING_SM, side="bottom",
        )

        ctk.CTkButton(
            bottom_frame,
            text="  \u23FB   Log Out",
            font=FONT_BODY,
            fg_color="transparent",
            hover_color=_LOGOUT_RED_HOVER,
            text_color=_LOGOUT_RED,
            anchor="w",
            height=36,
            corner_radius=6,
            command=self._on_logout,
        ).pack(fill="x")

    @staticmethod
    def _get_initials(full_name: str) -> str:
        """Extract up to two uppercase initials from a full name."""
        parts = full_name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][0].upper()
        return "?"
