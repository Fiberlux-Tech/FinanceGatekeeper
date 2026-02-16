"""
Structured JSON Logging Module.

Provides a StructuredLogger factory that produces logging.Logger instances
configured with JSON-formatted output for the audit trail.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, TextIO, Union


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON objects.

    Each log entry contains:
        - timestamp  (ISO-8601, UTC)
        - level      (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        - logger_name
        - message
        - extra      (optional structured fields passed via the `extra` kwarg)
    """

    # Pre-compute the set of standard LogRecord attribute names once at
    # class definition time, avoiding a fresh LogRecord allocation on
    # every ``format()`` call (M-5 thread-safety / performance fix).
    _STANDARD_ATTRS: frozenset[str] = frozenset(
        logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        ).__dict__.keys()
    )

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
        extra_fields: dict[str, str] = {
            key: str(value)
            for key, value in record.__dict__.items()
            if key not in self._STANDARD_ATTRS
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

    # Default log file configuration
    _DEFAULT_LOG_FILE: str = "gatekeeper.log"

    def __init__(
        self,
        name: str = "gatekeeper",
        level: int = logging.INFO,
        stream: Union[TextIO, None] = None,
        log_file: Optional[str] = None,
        max_bytes: Optional[int] = None,
        backup_count: Optional[int] = None,
    ) -> None:
        # Lazy import to avoid circular dependency at module level
        from app.config import get_config
        _cfg = get_config()

        self._logger: logging.Logger = logging.getLogger(name)
        self._logger.setLevel(level)

        resolved_max_bytes: int = max_bytes if max_bytes is not None else _cfg.LOG_MAX_BYTES
        resolved_backup_count: int = backup_count if backup_count is not None else _cfg.LOG_BACKUP_COUNT

        # Prevent duplicate handlers when the same name is reused.
        if not self._logger.handlers:
            formatter = JSONFormatter()

            # Stream handler (stdout)
            stream_handler = logging.StreamHandler(stream or sys.stdout)
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            self._logger.addHandler(stream_handler)

            # Rotating file handler — graceful fallback on permission errors
            resolved_log_file: str = log_file or self._DEFAULT_LOG_FILE
            try:
                log_path = Path(resolved_log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    filename=str(log_path),
                    maxBytes=resolved_max_bytes,
                    backupCount=resolved_backup_count,
                    encoding="utf-8",
                )
                file_handler.setLevel(level)
                file_handler.setFormatter(formatter)
                self._logger.addHandler(file_handler)
            except (PermissionError, OSError) as exc:
                self._logger.warning(
                    "Could not create log file '%s': %s. "
                    "Continuing with console logging only.",
                    resolved_log_file,
                    exc,
                )

    # -- Public attribute -----------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        """Access the underlying ``logging.Logger`` directly."""
        return self._logger

    # -- Convenience delegates ------------------------------------------------

    def debug(self, msg: str, *args: object, **kwargs: object) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: object, **kwargs: object) -> None:
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args: object, **kwargs: object) -> None:
        self._logger.critical(msg, *args, **kwargs)


def get_logger(name: str = "gatekeeper") -> StructuredLogger:
    """Create and return a ``StructuredLogger`` instance with the given *name*.

    This is a thin convenience factory.  Prefer direct instantiation of
    ``StructuredLogger`` when full control over ``level`` and ``stream``
    is needed.
    """
    return StructuredLogger(name=name)
