"""General Utility Functions."""

from __future__ import annotations

import ctypes
import math
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Protocol, Union, runtime_checkable

__all__ = ["convert_to_json_safe", "secure_clear_string"]


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

JsonSafeType = Union[
    None,
    str,
    int,
    bool,
    float,
    Dict[str, "JsonSafeType"],
    List["JsonSafeType"],
]
"""The set of types that are natively representable in JSON."""


@runtime_checkable
class PydanticLike(Protocol):
    """Protocol for objects that expose a Pydantic-style ``model_dump`` method."""

    def model_dump(self) -> Dict[str, "JsonInputType"]: ...  # noqa: E704


JsonInputType = Union[
    None,
    str,
    int,
    bool,
    float,
    Decimal,
    datetime,
    date,
    Dict[str, "JsonInputType"],
    List["JsonInputType"],
    PydanticLike,
]
"""All types accepted as input to :func:`convert_to_json_safe`."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_to_json_safe(data: JsonInputType) -> JsonSafeType:
    """Recursively convert a data structure to JSON-safe types.

    Handles:
    - ``datetime`` / ``date`` objects -> ISO-format strings
    - ``Decimal`` -> ``float``
    - ``float`` NaN / Inf -> ``None``
    - Nested dicts and lists
    - Pydantic models (via ``.model_dump()``)
    """
    if data is None:
        return None

    if isinstance(data, (str, int, bool)):
        return data

    if isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return None
        return data

    if isinstance(data, Decimal):
        return float(data)

    # datetime MUST be checked before date because datetime is a subclass of date.
    if isinstance(data, datetime):
        return data.isoformat()

    if isinstance(data, date):
        return data.isoformat()

    if isinstance(data, dict):
        return {key: convert_to_json_safe(value) for key, value in data.items()}

    if isinstance(data, (list, tuple)):
        return [convert_to_json_safe(item) for item in data]

    # Handle Pydantic models via structural typing (Protocol).
    if isinstance(data, PydanticLike):
        return convert_to_json_safe(data.model_dump())

    # Fallback: convert to string for any unrecognised type.
    # This is an intentional safety net — it prevents a hard crash when an
    # unexpected type reaches the serialization boundary.  Callers MUST ensure
    # only known types (as listed in ``JsonInputType``) are passed.  Common
    # culprits that need pre-conversion before reaching this function:
    #   - ``uuid.UUID``  → call ``str(uuid_val)`` before passing
    #   - ``pathlib.Path`` → call ``str(path_val)`` or ``.as_posix()``
    #   - ``bytes``       → decode or base64-encode before passing
    return str(data)


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def secure_clear_string(value: str) -> None:
    """Best-effort overwrite of a Python string's internal buffer.

    Python strings are immutable and cannot be zeroed in the normal
    sense.  This function uses ``ctypes.memset`` to overwrite the
    CPython internal buffer *after* the caller has finished using the
    string.  This is a **defence-in-depth** measure — it is NOT a
    guarantee, because:

    - The garbage collector may have already copied the data.
    - Interned short strings or strings used as dict keys are shared.
    - The OS may page memory to disk before we clear it.

    Despite these caveats, zeroing the buffer reduces the window of
    exposure in a memory dump or crash dump scenario (L-51).

    Parameters
    ----------
    value:
        The string whose underlying buffer should be overwritten.
        The caller should discard all references to this string
        immediately after calling this function — the object is
        corrupted and must not be read again.
    """
    if sys.implementation.name != "cpython":
        # ctypes.memset on the string buffer is only safe on CPython.
        return

    if not value:
        return

    try:
        # On CPython, id(obj) returns the memory address of the object.
        # The internal PyUnicodeObject stores its data after the object
        # header.  sys.getsizeof gives the total allocated size.
        size: int = sys.getsizeof(value)
        ctypes.memset(id(value), 0, size)
    except Exception:
        # If anything goes wrong (non-CPython, restricted environment),
        # silently continue — this is best-effort only.
        pass
