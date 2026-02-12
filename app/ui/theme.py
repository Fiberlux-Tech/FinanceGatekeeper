"""UI Theme Constants for FinanceGatekeeper.

Centralises all colour, font, and sizing constants for the
CustomTkinter interface.  Dark sidebar + light content area
design following finance-industry conventions.

This file contains **zero logic** — only ``Final`` constants.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Colour palette — dark sidebar + light content
# ---------------------------------------------------------------------------

SIDEBAR_BG: Final[str] = "#1a1a2e"
SIDEBAR_HOVER: Final[str] = "#16213e"
SIDEBAR_ACTIVE: Final[str] = "#0f3460"
SIDEBAR_TEXT: Final[str] = "#e0e0e0"

CONTENT_BG: Final[str] = "#f0f0f0"
CONTENT_CARD_BG: Final[str] = "#ffffff"

ACCENT_PRIMARY: Final[str] = "#5B4FCF"
ACCENT_HOVER: Final[str] = "#4A3FBF"
TEXT_PRIMARY: Final[str] = "#1a1a2e"
TEXT_SECONDARY: Final[str] = "#6c757d"
TEXT_LIGHT: Final[str] = "#ffffff"

# Status indicators
STATUS_ONLINE: Final[str] = "#27ae60"
STATUS_OFFLINE: Final[str] = "#e74c3c"
STATUS_SYNCING: Final[str] = "#f39c12"

# Input / form
INPUT_BG: Final[str] = "#ffffff"
INPUT_BORDER: Final[str] = "#ced4da"
ERROR_TEXT: Final[str] = "#dc3545"
SUCCESS_TEXT: Final[str] = "#27ae60"

# Tab / interactive
TAB_BORDER: Final[str] = "#e0e0e0"
TAB_HOVER: Final[str] = "#f0f0f0"
LOGOUT_PRIMARY: Final[str] = "#e74c3c"
LOGOUT_HOVER: Final[str] = "#3a1a1a"

# ---------------------------------------------------------------------------
# Fonts (Segoe UI — Windows default, fallback to system)
# ---------------------------------------------------------------------------

FONT_FAMILY: Final[str] = "Segoe UI"
FONT_BRAND: Final[tuple[str, int, str]] = (FONT_FAMILY, 22, "bold")
FONT_ICON_LG: Final[tuple[str, int, str]] = (FONT_FAMILY, 24, "bold")
FONT_HEADING: Final[tuple[str, int, str]] = (FONT_FAMILY, 20, "bold")
FONT_SUBTITLE: Final[tuple[str, int]] = (FONT_FAMILY, 12)
FONT_BODY: Final[tuple[str, int]] = (FONT_FAMILY, 13)
FONT_SIDEBAR: Final[tuple[str, int]] = (FONT_FAMILY, 14)
FONT_SIDEBAR_ACTIVE: Final[tuple[str, int, str]] = (FONT_FAMILY, 14, "bold")
FONT_LABEL: Final[tuple[str, int, str]] = (FONT_FAMILY, 11, "bold")
FONT_SMALL: Final[tuple[str, int]] = (FONT_FAMILY, 11)
FONT_CAPTION: Final[tuple[str, int]] = (FONT_FAMILY, 10)
FONT_BUTTON: Final[tuple[str, int, str]] = (FONT_FAMILY, 13, "bold")

# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

SIDEBAR_WIDTH: Final[int] = 250
STATUS_BAR_HEIGHT: Final[int] = 30
LOGIN_WINDOW_WIDTH: Final[int] = 480
LOGIN_WINDOW_HEIGHT: Final[int] = 780
MAIN_WINDOW_WIDTH: Final[int] = 1200
MAIN_WINDOW_HEIGHT: Final[int] = 750
CORNER_RADIUS: Final[int] = 8
PADDING_SM: Final[int] = 8
PADDING_MD: Final[int] = 16
PADDING_LG: Final[int] = 24
