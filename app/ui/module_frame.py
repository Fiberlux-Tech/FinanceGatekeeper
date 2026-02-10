"""Module Protocol and Base Frame.

Defines the interface that all pluggable modules must implement.
The Host Shell uses this protocol for dynamic module loading and
sidebar registration.

Pattern reference: ``app.utils.general.PydanticLike`` (same
``@runtime_checkable Protocol`` approach).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import customtkinter as ctk


@runtime_checkable
class ModuleProtocol(Protocol):
    """Contract that every pluggable module must satisfy.

    The Host Shell calls these methods to manage module lifecycle.
    Using ``Protocol`` (structural subtyping / PEP 544) rather than
    ABC so that modules do not need to inherit from a specific base
    class — they just need the right shape.
    """

    @property
    def module_id(self) -> str:
        """Unique identifier for this module (e.g. ``'gatekeeper'``)."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in the sidebar."""
        ...

    @property
    def icon(self) -> str:
        """Unicode character used as the sidebar icon."""
        ...

    def get_frame(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        """Return the root ``CTkFrame`` for this module's UI.

        Called by the ModuleSwitcher when this module is activated.
        The frame is cached after first creation.

        Parameters
        ----------
        parent:
            The content container provided by the Host Shell.
        """
        ...
