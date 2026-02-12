"""
File Watcher Service.

Real-time monitoring of the ``01_Inbox`` directory tree using the
``watchdog`` library.  Detects ``.xlsx`` file creation, modification,
and deletion and emits typed ``FileEvent`` objects to a registered
callback.

The observer runs on a daemon thread and is started/stopped by the
``AppShell`` in response to login/logout events.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import AppConfig
from app.logger import StructuredLogger
from app.models.enums import FileEventType, FileStatus
from app.models.file_models import FileEvent, InboxFile
from app.services.base_service import BaseService
from app.services.file_guards import FileGuardsService

_XLSX_SUFFIX: str = ".xlsx"
_TEMP_PREFIX: str = "~$"


class _InboxEventHandler(FileSystemEventHandler):
    """Watchdog handler that filters for ``.xlsx`` changes.

    Parameters
    ----------
    on_event:
        Callback invoked (from the observer thread) with a ``FileEvent``.
    logger:
        Structured logger.
    """

    def __init__(
        self,
        on_event: Callable[[FileEvent], None],
        logger: StructuredLogger,
    ) -> None:
        super().__init__()
        self._on_event = on_event
        self._logger = logger

    # ------------------------------------------------------------------
    # Watchdog overrides
    # ------------------------------------------------------------------

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event, FileEventType.CREATED)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event, FileEventType.MODIFIED)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event, FileEventType.DELETED)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle(self, event: FileSystemEvent, event_type: FileEventType) -> None:
        if event.is_directory:
            return

        path = Path(str(event.src_path))
        if path.suffix.lower() != _XLSX_SUFFIX:
            return
        if path.name.startswith(_TEMP_PREFIX):
            return

        inbox_file = _build_inbox_file(path, event_type)
        if inbox_file is None:
            return

        self._logger.debug(
            "Watchdog %s: %s",
            event_type.value,
            path.name,
        )

        file_event = FileEvent(
            event_type=event_type,
            file=inbox_file,
            timestamp=datetime.now(tz=timezone.utc),
        )
        self._on_event(file_event)


class FileWatcherService(BaseService):
    """Observe the inbox for ``.xlsx`` file changes.

    Parameters
    ----------
    inbox_path:
        Absolute path to the ``01_Inbox`` directory.
    file_guards:
        Safety-guards service for on-demand file status checks.
    config:
        Application configuration (polling interval).
    logger:
        Structured logger instance.
    """

    def __init__(
        self,
        inbox_path: Path,
        file_guards: FileGuardsService,
        config: AppConfig,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._inbox_path = inbox_path
        self._file_guards = file_guards
        self._config = config

        self._observer: Optional[Observer] = None
        self._callback: Optional[Callable[[FileEvent], None]] = None
        self._callback_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog observer on a daemon thread.

        Safe to call multiple times — duplicate calls are no-ops.
        """
        if self._observer is not None and self._observer.is_alive():
            self._logger.debug("File watcher already running.")
            return

        handler = _InboxEventHandler(
            on_event=self._dispatch_event,
            logger=self._logger,
        )

        self._observer = Observer()
        self._observer.daemon = True
        self._observer.schedule(
            handler,
            str(self._inbox_path),
            recursive=True,
        )
        self._observer.start()
        self._logger.info(
            "File watcher started on: %s", self._inbox_path,
        )

    def stop(self) -> None:
        """Stop the watchdog observer and wait for its thread to finish.

        Safe to call when the observer is not running.
        """
        if self._observer is None:
            return

        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._observer = None
        self._logger.info("File watcher stopped.")

    @property
    def is_running(self) -> bool:
        """``True`` if the observer thread is alive."""
        return self._observer is not None and self._observer.is_alive()

    def set_callback(self, callback: Callable[[FileEvent], None]) -> None:
        """Register a callback for file events.

        The callback is invoked **from the watchdog thread**.  UI
        consumers must marshal to the main thread (e.g. via
        ``widget.after()``).
        """
        with self._callback_lock:
            self._callback = callback

    def get_inbox_files(self) -> list[InboxFile]:
        """Return a snapshot of all ``.xlsx`` files in the inbox.

        All files sit directly in the flat ``01_INBOX`` directory.
        Business unit is determined later from the Excel contents.
        """
        files: list[InboxFile] = []

        try:
            for child in self._inbox_path.iterdir():
                if (
                    child.is_file()
                    and child.suffix.lower() == _XLSX_SUFFIX
                    and not child.name.startswith(_TEMP_PREFIX)
                ):
                    inbox_file = _stat_inbox_file(child)
                    if inbox_file is not None:
                        files.append(inbox_file)
        except OSError as exc:
            self._logger.warning("Error scanning inbox: %s", exc)

        return files

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch_event(self, event: FileEvent) -> None:
        """Forward *event* to the registered callback (if any)."""
        with self._callback_lock:
            cb = self._callback

        if cb is not None:
            cb(event)


# ======================================================================
# Module-level helpers
# ======================================================================


def _build_inbox_file(
    path: Path,
    event_type: FileEventType,
) -> Optional[InboxFile]:
    """Create an ``InboxFile`` from a watchdog event path.

    Business unit is left as ``None`` — it will be resolved later when
    the Excel file is parsed.
    """
    if event_type == FileEventType.DELETED:
        return InboxFile(
            path=path,
            filename=path.name,
            size_bytes=0,
            modified_at=datetime.now(tz=timezone.utc),
            status=FileStatus.READY,
        )

    return _stat_inbox_file(path)


def _stat_inbox_file(path: Path) -> Optional[InboxFile]:
    """Build an ``InboxFile`` with live ``stat`` data.

    Returns ``None`` if the file disappears before we can stat it.
    """
    try:
        st = path.stat()
    except OSError:
        return None

    return InboxFile(
        path=path,
        filename=path.name,
        size_bytes=st.st_size,
        modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        status=FileStatus.READY,
    )
