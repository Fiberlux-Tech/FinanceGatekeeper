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
        # --- User info section ---
        user_frame = ctk.CTkFrame(self, fg_color="transparent")
        user_frame.pack(fill="x", padx=PADDING_MD, pady=(PADDING_MD, PADDING_SM))

        user = self._session.get_current_user()
        ctk.CTkLabel(
            user_frame,
            text=user.full_name,
            font=FONT_SIDEBAR_ACTIVE,
            text_color=TEXT_LIGHT,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            user_frame,
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

        # --- Bottom section: logout ---
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=PADDING_MD, pady=PADDING_MD, side="bottom")

        ctk.CTkButton(
            bottom_frame,
            text="Logout",
            font=FONT_BODY,
            fg_color="transparent",
            hover_color=SIDEBAR_HOVER,
            text_color=SIDEBAR_TEXT,
            anchor="w",
            height=36,
            command=self._on_logout,
        ).pack(fill="x")
