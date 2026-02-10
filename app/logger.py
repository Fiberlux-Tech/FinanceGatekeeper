"""
Structured JSON Logging Module.

Provides a StructuredLogger factory that produces logging.Logger instances
configured with JSON-formatted output for the audit trail.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TextIO, Union


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON objects.

    Each log entry contains:
        - timestamp  (ISO-8601, UTC)
        - level      (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        - logger_name
        - message
        - extra      (optional structured fields passed via the `extra` kwarg)
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Union[str, dict[str, str]]] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "message": record.getMessage(),
        }

        # Capture any structured extra fields attached by the caller.
        # Standard LogRecord attributes are excluded so only user-supplied
        # context appears under the "extra" key.
        standard_attrs: frozenset[str] = frozenset(
            logging.LogRecord(
                name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
            ).__dict__.keys()
        )
        extra_fields: dict[str, str] = {
            key: str(value)
            for key, value in record.__dict__.items()
            if key not in standard_attrs
        }
        if extra_fields:
            entry["extra"] = extra_fields

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text

        return json.dumps(entry, ensure_ascii=False)


class StructuredLogger:
    """Injectable logger factory.

    Instantiate this class and pass the resulting object wherever a logger
    is needed.  The underlying ``logging.Logger`` is exposed via the
    ``.logger`` attribute and standard convenience methods are delegated
    directly.

    Usage::

        log = StructuredLogger(name="gatekeeper")
        log.info("Server started", extra={"port": "8080"})

    Dependency Injection::

        class SomeService:
            def __init__(self, logger: StructuredLogger) -> None:
                self._log = logger
    """

    def __init__(
        self,
        name: str = "gatekeeper",
        level: int = logging.DEBUG,
        stream: Union[TextIO, None] = None,
    ) -> None:
        self._logger: logging.Logger = logging.getLogger(name)
        self._logger.setLevel(level)

        # Prevent duplicate handlers when the same name is reused.
        if not self._logger.handlers:
            handler = logging.StreamHandler(stream or sys.stdout)
            handler.setLevel(level)
            handler.setFormatter(JSONFormatter())
            self._logger.addHandler(handler)

    # -- Public attribute -----------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        """Access the underlying ``logging.Logger`` directly."""
        return self._logger

    # -- Convenience delegates ------------------------------------------------

    def debug(self, msg: str, **kwargs: str) -> None:
        self._logger.debug(msg, **kwargs)

    def info(self, msg: str, **kwargs: str) -> None:
        self._logger.info(msg, **kwargs)

    def warning(self, msg: str, **kwargs: str) -> None:
        self._logger.warning(msg, **kwargs)

    def error(self, msg: str, **kwargs: str) -> None:
        self._logger.error(msg, **kwargs)

    def critical(self, msg: str, **kwargs: str) -> None:
        self._logger.critical(msg, **kwargs)


def get_logger(name: str = "gatekeeper") -> StructuredLogger:
    """Create and return a ``StructuredLogger`` instance with the given *name*.

    This is a thin convenience factory.  Prefer direct instantiation of
    ``StructuredLogger`` when full control over ``level`` and ``stream``
    is needed.
    """
    return StructuredLogger(name=name)
