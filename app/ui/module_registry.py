"""Module Registry.

Central registry for all application modules.  The Host Shell queries
this registry at startup to populate the sidebar and configure the
module switcher.

Adding a new module = one ``register()`` call + one view class.
Zero core file modifications required.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app.logger import StructuredLogger


class ModuleEntry:
    """Metadata for a single registered module.

    Attributes
    ----------
    module_id:
        Unique string identifier (e.g. ``'gatekeeper'``).
    display_name:
        Human-readable name shown in the sidebar.
    icon:
        Unicode character used as the sidebar icon.
    factory:
        Callable that receives a parent ``CTkFrame`` and returns the
        module's root frame.  Called lazily on first activation.
    required_roles:
        Set of ``UserRole`` string values that may access this module.
    """

    __slots__ = (
        "module_id",
        "display_name",
        "icon",
        "factory",
        "required_roles",
    )

    def __init__(
        self,
        module_id: str,
        display_name: str,
        icon: str,
        factory: Callable[[ctk.CTkFrame], ctk.CTkFrame],
        required_roles: frozenset[str],
    ) -> None:
        self.module_id = module_id
        self.display_name = display_name
        self.icon = icon
        self.factory = factory
        self.required_roles = required_roles


class ModuleRegistry:
    """Manages the collection of registered modules.

    The application entry-point creates a ``ModuleRegistry``, registers
    all modules, and passes it to the ``AppShell``.  The shell then
    queries the registry to build the sidebar and handle module switching.

    Parameters
    ----------
    logger:
        Structured logger for registration events.
    """

    def __init__(self, logger: StructuredLogger) -> None:
        self._entries: dict[str, ModuleEntry] = {}
        self._logger = logger
        self._default_module_id: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        module_id: str,
        display_name: str,
        icon: str,
        factory: Callable[[ctk.CTkFrame], ctk.CTkFrame],
        required_roles: frozenset[str] = frozenset({"SALES", "FINANCE", "ADMIN"}),
        *,
        default: bool = False,
    ) -> None:
        """Register a module with the host shell.

        Parameters
        ----------
        module_id:
            Unique identifier for the module.
        display_name:
            Label shown in the sidebar.
        icon:
            Unicode icon character for the sidebar entry.
        factory:
            Callable ``(parent) -> CTkFrame`` invoked lazily on first use.
        required_roles:
            Roles permitted to access this module.
        default:
            If ``True``, this module is activated after login.
        """
        if module_id in self._entries:
            self._logger.warning(
                "Module '%s' already registered; overwriting.", module_id,
            )
        self._entries[module_id] = ModuleEntry(
            module_id=module_id,
            display_name=display_name,
            icon=icon,
            factory=factory,
            required_roles=required_roles,
        )
        if default or not self._default_module_id:
            self._default_module_id = module_id
        self._logger.info("Module registered: %s (%s)", module_id, display_name)

    def get_modules_for_role(self, role: str) -> list[ModuleEntry]:
        """Return modules visible to *role*, preserving registration order."""
        return [
            entry
            for entry in self._entries.values()
            if role in entry.required_roles
        ]

    def get_module(self, module_id: str) -> ModuleEntry:
        """Return a specific module entry by ID.

        Raises
        ------
        KeyError
            If *module_id* is not registered.
        """
        if module_id not in self._entries:
            raise KeyError(f"Module '{module_id}' is not registered.")
        return self._entries[module_id]

    @property
    def default_module_id(self) -> str:
        """The ``module_id`` to activate after login."""
        return self._default_module_id
