"""Shared utility functions and models for the FinanceGatekeeper application.

This package provides convenience re-exports so that consumers can import
directly from ``app.utils`` (e.g. ``from app.utils import normalize_keys``)
while full absolute imports (e.g. ``from app.utils.string_helpers import
normalize_keys``) remain supported.
"""

from app.utils.audit import AuditEvent, log_audit_event
from app.utils.general import convert_to_json_safe
from app.utils.math_utils import calculate_irr, calculate_npv
from app.utils.string_helpers import (
    denormalize_keys,
    normalize_keys,
    to_camel_case,
    to_snake_case,
)

__all__ = [
    "AuditEvent",
    "calculate_irr",
    "calculate_npv",
    "convert_to_json_safe",
    "denormalize_keys",
    "log_audit_event",
    "normalize_keys",
    "to_camel_case",
    "to_snake_case",
]
