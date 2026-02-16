"""
Sync Worker Service.

Background daemon thread that replays pending ``sync_queue`` entries to
Supabase.  Follows the same daemon-thread lifecycle pattern as
:class:`FileWatcherService`: the caller invokes :meth:`start` /
:meth:`stop`, and the worker thread polls the local SQLite queue at a
configurable interval with exponential backoff on consecutive failures.

The ``sync_queue`` table acts as a write-ahead log: every local mutation
is enqueued there first, and this service replays those mutations to
Supabase when connectivity is available.  This guarantees offline-first
semantics — the application never blocks on a network call for writes.

Thread Safety
-------------
All SQLite writes acquire ``DatabaseManager.write_lock`` (an
``RLock``) before executing, ensuring serialised access from the
main thread, the sync worker, and any other background services.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from app.config import AppConfig
from app.database import DatabaseManager
from app.logger import StructuredLogger
from app.services.base_service import BaseService


class SyncWorkerService(BaseService):
    """Daemon thread that drains the local ``sync_queue`` to Supabase.

    Parameters
    ----------
    db:
        Initialised ``DatabaseManager`` providing ``.supabase``,
        ``.sqlite``, ``.write_lock``, and ``.is_online`` access.
    config:
        Application configuration (injected for future tunables).
    logger:
        Structured JSON logger.
    """

    # ------------------------------------------------------------------
    # Class constants
    # ------------------------------------------------------------------

    _BASE_INTERVAL_S: float = 30.0
    _MAX_INTERVAL_S: float = 300.0  # 5-minute cap
    _BATCH_SIZE: int = 50
    _MAX_RETRY_COUNT: int = 5

    _ALLOWED_TABLES: frozenset[str] = frozenset({
        "transactions",
        "fixed_costs",
        "recurring_services",
        "audit_log",
        "master_variables",
        "profiles",
    })

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        db: DatabaseManager,
        config: AppConfig,
        logger: StructuredLogger,
    ) -> None:
        super().__init__(logger)
        self._db: DatabaseManager = db
        self._config: AppConfig = config
        self._thread: Optional[threading.Thread] = None
        self._stop_event: threading.Event = threading.Event()
        self._consecutive_failures: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the sync worker on a daemon thread.

        Idempotent — calling ``start()`` when the worker is already
        running is a no-op.
        """
        if self._thread is not None and self._thread.is_alive():
            self._logger.debug("Sync worker already running.")
            return

        self._stop_event.clear()
        self._consecutive_failures = 0

        self._thread = threading.Thread(
            target=self._run_loop,
            name="SyncWorker",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Sync worker started.")

    def stop(self) -> None:
        """Signal the worker to stop and wait up to 10 s for it to exit.

        Safe to call when the worker is not running.
        """
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=10.0)

        if self._thread.is_alive():
            self._logger.warning(
                "Sync worker thread did not terminate within 10 s."
            )
        else:
            self._logger.info("Sync worker stopped.")

        self._thread = None

    @property
    def is_running(self) -> bool:
        """``True`` when the worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main loop executed on the daemon thread.

        Wrapped in a top-level ``try/except`` so that an unexpected
        exception logs an error rather than silently killing the thread.
        """
        try:
            while not self._stop_event.is_set():
                interval = self._calculate_backoff_interval()
                if self._stop_event.wait(timeout=interval):
                    break  # Stop requested

                if not self._db.is_online:
                    continue

                try:
                    synced = self._process_pending_queue()
                    if synced > 0:
                        self._consecutive_failures = 0
                except Exception:
                    self._consecutive_failures += 1
                    self._logger.warning(
                        "Sync cycle failed", exc_info=True,
                    )
        except Exception:
            self._logger.error(
                "Sync worker thread terminated due to unhandled exception.",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Queue processing
    # ------------------------------------------------------------------

    def _process_pending_queue(self) -> int:
        """Read pending rows, replay to Supabase, mark synced/failed.

        Returns
        -------
        int
            Number of rows successfully synced in this cycle.
        """
        with self._db.write_lock:
            rows = self._db.sqlite.execute(
                """
                SELECT id, table_name, operation, entity_id, payload
                FROM sync_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (self._BATCH_SIZE,),
            ).fetchall()

        if not rows:
            return 0

        synced_count: int = 0

        for row in rows:
            queue_id: int = row["id"]
            table_name: str = row["table_name"]
            operation: str = row["operation"]
            entity_id: str = row["entity_id"]
            raw_payload: str = row["payload"]

            try:
                payload: dict[str, object] | list[dict[str, object]] = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError) as exc:
                self._logger.error(
                    "Malformed JSON payload in sync_queue row %d: %s",
                    queue_id,
                    exc,
                )
                self._mark_failed(queue_id, f"Malformed JSON: {exc}")
                continue

            try:
                self._replay_operation(table_name, operation, entity_id, payload)
                self._mark_synced(queue_id)
                synced_count += 1
                self._logger.debug(
                    "Synced queue row %d: %s.%s(%s)",
                    queue_id,
                    table_name,
                    operation,
                    entity_id,
                )
            except Exception as exc:
                self._logger.warning(
                    "Failed to sync queue row %d: %s", queue_id, exc,
                )
                self._mark_failed(queue_id, str(exc))

        if synced_count > 0:
            self._logger.info(
                "Sync cycle complete: %d/%d rows synced.", synced_count, len(rows),
            )

        return synced_count

    # ------------------------------------------------------------------
    # Operation dispatcher
    # ------------------------------------------------------------------

    def _replay_operation(
        self,
        table_name: str,
        operation: str,
        entity_id: str,
        payload: dict[str, object] | list[dict[str, object]],
    ) -> None:
        """Replay a single queued operation to Supabase.

        Parameters
        ----------
        table_name:
            Target Supabase table (must be in ``_ALLOWED_TABLES``).
        operation:
            One of ``insert``, ``update``, ``update_status``, ``upsert``,
            ``replace``.
        entity_id:
            Primary key or ``transaction_id`` used for WHERE clauses.
        payload:
            Deserialised JSON payload (dict for most ops, list for
            ``replace`` batch operations).

        Raises
        ------
        ValueError
            If the table or operation is not recognised.
        """
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Disallowed sync target table: {table_name}")

        supabase = self._db.supabase

        if operation == "insert":
            supabase.table(table_name).insert(payload).execute()

        elif operation in ("update", "update_status"):
            supabase.table(table_name).update(payload).eq("id", entity_id).execute()

        elif operation == "upsert":
            supabase.table(table_name).upsert(payload).execute()

        elif operation == "replace":
            if isinstance(payload, list):
                supabase.table(table_name).delete().eq(
                    "transaction_id", entity_id,
                ).execute()
                for item in payload:
                    supabase.table(table_name).insert(item).execute()
            else:
                supabase.table(table_name).upsert(payload).execute()

        else:
            raise ValueError(f"Unknown sync operation: {operation}")

    # ------------------------------------------------------------------
    # Exponential backoff
    # ------------------------------------------------------------------

    def _calculate_backoff_interval(self) -> float:
        """Return the sleep interval for the current failure count.

        On zero failures the base interval is used.  Each consecutive
        failure doubles the interval (capped at ``_MAX_INTERVAL_S``).
        """
        if self._consecutive_failures == 0:
            return self._BASE_INTERVAL_S

        backoff = self._BASE_INTERVAL_S * (2 ** min(self._consecutive_failures, 6))
        return min(backoff, self._MAX_INTERVAL_S)

    # ------------------------------------------------------------------
    # Mark helpers (direct SQLite, with write_lock)
    # ------------------------------------------------------------------

    def _mark_synced(self, queue_id: int) -> None:
        """Mark a ``sync_queue`` entry as synced."""
        with self._db.write_lock:
            self._db.sqlite.execute(
                """
                UPDATE sync_queue
                SET status = 'synced', attempted_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (queue_id,),
            )
            self._db.sqlite.commit()

    def _mark_failed(self, queue_id: int, error_message: str) -> None:
        """Mark a ``sync_queue`` entry as failed and apply retry logic.

        If the row has already been attempted ``_MAX_RETRY_COUNT`` times
        the status is set to ``permanently_failed`` so it is never
        retried again.  Otherwise the status is reset to ``pending`` for
        the next cycle.
        """
        with self._db.write_lock:
            row = self._db.sqlite.execute(
                "SELECT error_message FROM sync_queue WHERE id = ?",
                (queue_id,),
            ).fetchone()

            retry_count: int = 0
            if row and row["error_message"]:
                retry_count = str(row["error_message"]).count("Attempt ") + 1

            if retry_count >= self._MAX_RETRY_COUNT:
                status = "permanently_failed"
            else:
                status = "pending"

            self._db.sqlite.execute(
                """
                UPDATE sync_queue
                SET status = ?, attempted_at = CURRENT_TIMESTAMP,
                    error_message = ?
                WHERE id = ?
                """,
                (status, f"Attempt {retry_count + 1}: {error_message}", queue_id),
            )
            self._db.sqlite.commit()
