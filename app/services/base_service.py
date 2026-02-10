"""
Base Service Class.

Minimal base class standardizing the logger pattern for all services.
Services extend this and add their own repository dependencies via __init__.
"""

from __future__ import annotations

from app.logger import StructuredLogger


class BaseService:
    """Base class for all service classes. Provides a logger."""

    def __init__(self, logger: StructuredLogger) -> None:
        self._logger: StructuredLogger = logger
