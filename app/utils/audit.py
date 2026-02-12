"""
Structured Audit Logging Utility.

Per CLAUDE.md mandate: "Log every state change as a structured JSON object."
Provides a Pydantic-validated model and a single function for consistent
audit trail entries.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Union

from pydantic import BaseModel, Field

from app.logger import StructuredLogger

__all__ = ["AuditEvent", "log_audit_event", "persist_audit_event"]

# ---------------------------------------------------------------------------
# Scalar type permitted inside the ``details`` mapping.  Kept deliberately
# flat -- nested structures should be modelled explicitly, not smuggled
# through the audit log.
# ---------------------------------------------------------------------------
DetailValue = Union[str, int, float, bool, None]


class AuditEvent(BaseModel):
    """Schema-validated representation of a single audit trail entry.

    Every audit event is validated against this model before it is
    serialised to JSON and handed to the logger, guaranteeing that
    malformed payloads are caught at the point of origin rather than
    downstream.
    """

    timestamp: str
    action: str
    entity_type: str
    entity_id: str
    user_id: str
    details: dict[str, DetailValue] = Field(default_factory=dict)


def log_audit_event(
    logger: StructuredLogger,
    action: str,
    entity_type: str,
    entity_id: str,
    user_id: str,
    details: Optional[dict[str, DetailValue]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Log a structured JSON audit event, with optional SQLite persistence.

    Always emits a structured JSON log line via *logger*.  When *conn* is
    provided, also writes the event to the ``audit_log`` SQLite table for
    queryable persistence (dual logging).

    The *conn* parameter is optional for backward compatibility — existing
    callers that omit it continue to work as before (log-only).

    Args:
        logger: The logger instance to write to.
        action: What happened (e.g. ``"CREATE"``, ``"APPROVE"``,
            ``"REJECT"``, ``"UPDATE_ROLE"``).
        entity_type: Type of entity affected (e.g. ``"Transaction"``,
            ``"User"``, ``"MasterVariable"``).
        entity_id: Primary key of the affected entity.
        user_id: ID of the user who performed the action.
        details: Optional additional context (e.g. old/new values).
        conn: Optional SQLite connection.  When provided, the event is
            also persisted to the ``audit_log`` table via
            :func:`persist_audit_event`.
    """
    event = AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        details=details or {},
    )
    logger.info("AUDIT: %s", json.dumps(event.model_dump(), default=str))

    # Dual logging: persist to SQLite when a connection is available.
    # Errors are logged but never propagated — audit persistence must
    # not break the calling operation.
    if conn is not None:
        try:
            persist_audit_event(
                conn=conn,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=user_id,
                details=details,
            )
        except Exception as db_err:
            logger.warning(
                "Failed to persist audit event to SQLite: %s", db_err
            )


def persist_audit_event(
    conn: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: str,
    user_id: str,
    details: Optional[dict[str, DetailValue]] = None,
) -> None:
    """Write an audit event to the SQLite ``audit_log`` table.

    Complements ``log_audit_event()`` by providing persistent,
    queryable audit storage for the Timeline Dashboard (Phase 5).
    The event is Pydantic-validated before insertion.

    The function constructs an :class:`AuditEvent`, which performs full
    Pydantic validation, and then writes the validated fields into the
    ``audit_log`` table.  If validation fails a
    :class:`pydantic.ValidationError` will propagate to the caller --
    this is intentional so that malformed audit data is never silently
    persisted.

    Args:
        conn: An open SQLite connection with write access.
        action: What happened (e.g. ``"CREATE"``, ``"APPROVE"``).
        entity_type: Type of entity affected (e.g. ``"Transaction"``).
        entity_id: Primary key of the affected entity.
        user_id: ID of the user who performed the action.
        details: Optional additional context (e.g. old/new values).
    """
    event = AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        details=details or {},
    )

    conn.execute(
        """
        INSERT INTO audit_log (timestamp, action, entity_type, entity_id, user_id, details)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event.timestamp,
            event.action,
            event.entity_type,
            event.entity_id,
            event.user_id,
            json.dumps(event.details, default=str),
        ),
    )
    conn.commit()
