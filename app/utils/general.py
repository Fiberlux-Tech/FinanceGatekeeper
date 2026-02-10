"""General Utility Functions."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Protocol, Union, runtime_checkable

__all__ = ["convert_to_json_safe"]


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
    return str(data)
