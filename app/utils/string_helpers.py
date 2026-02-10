"""
String Helpers — Centralized Naming Convention Converter.

Single source of truth for key normalization (Section 4D of Technical Requirements).
All camelCase/PascalCase -> snake_case conversion flows through here.
Zero manual mapping: all key transformations must use these functions.
"""

from __future__ import annotations

import re
from typing import Union, overload

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "to_snake_case",
    "to_camel_case",
    "normalize_keys",
    "denormalize_keys",
]

# ---------------------------------------------------------------------------
# Recursive JSON value type (PEP 484 — no use of ``Any``)
# ---------------------------------------------------------------------------

JsonValue = Union[
    str,
    int,
    float,
    bool,
    None,
    dict[str, "JsonValue"],
    list["JsonValue"],
]

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (hot-path optimisation for large datasets)
# ---------------------------------------------------------------------------

# Inserts underscore between a run of uppercase letters and an uppercase
# letter followed by a lowercase letter.  e.g. "MRCoriginal" -> "MRC_original"
_RE_UPPER_RUN = re.compile(r"([A-Z]+)([A-Z][a-z])")

# Inserts underscore at the camelCase boundary where a lowercase letter or
# digit is followed by an uppercase letter.  e.g. "clientName" -> "client_Name"
_RE_CAMEL_BOUNDARY = re.compile(r"([a-z\d])([A-Z])")

# Collapses multiple consecutive underscores into a single one.
_RE_MULTI_UNDERSCORE = re.compile(r"_+")


# ---------------------------------------------------------------------------
# Case-conversion primitives
# ---------------------------------------------------------------------------


def to_snake_case(name: str) -> str:
    """
    Convert a camelCase, PascalCase, or mixed-case string to snake_case.

    Handles edge cases from legacy financial data keys:
        clientName       -> client_name
        unidadNegocio    -> unidad_negocio
        MRC_original     -> mrc_original
        ApprovalStatus   -> approval_status
        costoUnitario    -> costo_unitario
        CU1_original     -> cu1_original
        tipoCambio       -> tipo_cambio
        NRC_pen          -> nrc_pen
        grossMarginRatio -> gross_margin_ratio
    """
    # Insert underscore between consecutive uppercase and uppercase+lowercase
    s1 = _RE_UPPER_RUN.sub(r"\1_\2", name)
    # Insert underscore at camelCase boundary
    s2 = _RE_CAMEL_BOUNDARY.sub(r"\1_\2", s1)
    # Collapse multiple underscores and lowercase everything
    s3 = _RE_MULTI_UNDERSCORE.sub("_", s2)
    return s3.lower()


def to_camel_case(name: str) -> str:
    """
    Convert a snake_case string to camelCase.

    Preserves common financial acronyms:
        client_name     -> clientName
        mrc_original    -> mrcOriginal
        approval_status -> approvalStatus
    """
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


# ---------------------------------------------------------------------------
# Recursive key-normalisation helpers
# ---------------------------------------------------------------------------


@overload
def normalize_keys(data: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


@overload
def normalize_keys(data: list[JsonValue]) -> list[JsonValue]: ...


@overload
def normalize_keys(data: JsonValue) -> JsonValue: ...


def normalize_keys(
    data: Union[dict[str, JsonValue], list[JsonValue], JsonValue],
) -> Union[dict[str, JsonValue], list[JsonValue], JsonValue]:
    """
    Recursively convert all dictionary keys to snake_case.

    Used at the Repository/ingestion boundary to normalize incoming data
    from external sources (Excel, JSON APIs, Supabase) before it enters
    the Service and Model layers.
    """
    if isinstance(data, dict):
        return {to_snake_case(k): normalize_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [normalize_keys(item) for item in data]
    return data


@overload
def denormalize_keys(data: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


@overload
def denormalize_keys(data: list[JsonValue]) -> list[JsonValue]: ...


@overload
def denormalize_keys(data: JsonValue) -> JsonValue: ...


def denormalize_keys(
    data: Union[dict[str, JsonValue], list[JsonValue], JsonValue],
) -> Union[dict[str, JsonValue], list[JsonValue], JsonValue]:
    """
    Recursively convert all dictionary keys to camelCase.

    Used at the Repository/outbound boundary when writing data back
    to external systems that require camelCase (JSON APIs, Supabase).
    """
    if isinstance(data, dict):
        return {to_camel_case(k): denormalize_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [denormalize_keys(item) for item in data]
    return data
