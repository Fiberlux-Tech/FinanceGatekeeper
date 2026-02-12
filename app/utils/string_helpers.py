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
    "normalize_keys",
    "sanitize_postgrest_value",
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
    """Convert a camelCase, PascalCase, or mixed-case string to snake_case.

    Handles edge cases from legacy financial data keys::

        clientName       -> client_name
        unidadNegocio    -> unidad_negocio
        MRC_original     -> mrc_original
        ApprovalStatus   -> approval_status
        costoUnitario    -> costo_unitario
        CU1_original     -> cu1_original
        tipoCambio       -> tipo_cambio
        NRC_pen          -> nrc_pen
        grossMarginRatio -> gross_margin_ratio

    Known limitation — consecutive-uppercase acronyms
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    When an all-uppercase acronym is followed *directly* by a lowercase
    letter without a clear PascalCase boundary, the regex splits
    incorrectly.  For example:

    - ``XMLParser``  (correct PascalCase boundary) -> ``xml_parser``
    - ``XMLproperty`` (no boundary) -> ``xm_lproperty`` (wrong)

    Workaround: always use PascalCase boundaries after acronyms
    (e.g. ``XMLProperty``, not ``XMLproperty``).  This pattern is rare
    in the financial data keys processed by this application.
    """
    # Insert underscore between consecutive uppercase and uppercase+lowercase
    s1 = _RE_UPPER_RUN.sub(r"\1_\2", name)
    # Insert underscore at camelCase boundary
    s2 = _RE_CAMEL_BOUNDARY.sub(r"\1_\2", s1)
    # Collapse multiple underscores and lowercase everything
    s3 = _RE_MULTI_UNDERSCORE.sub("_", s2)
    return s3.lower()


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


# Characters unsafe for PostgREST filter interpolation: commas (OR
# predicates), periods (operator separators), parentheses (grouping),
# percent/underscore (SQL wildcards), backslash (ILIKE escape), colon
# (PostgREST relation/cast syntax).  Allowlist retains only alphanumeric
# characters, whitespace, hyphens, and accented Latin characters
# (U+00C0..U+024F — Spanish, Portuguese, French diacritics).
_POSTGREST_UNSAFE_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9\s\-\u00C0-\u024F]")


def sanitize_postgrest_value(value: str) -> str:
    """Strip characters unsafe for PostgREST filter interpolation.

    Retains only alphanumeric characters, whitespace, hyphens, and
    accented Latin characters (U+00C0..U+024F, covering Spanish,
    Portuguese, and French diacritics common in Peruvian business
    names).  All other characters — including PostgREST operators
    (``.``, ``,``, ``(``, ``)``), SQL wildcards (``%``, ``_``), and
    escape sequences (``\\``, ``:``) — are removed.

    Parameters
    ----------
    value:
        The raw user-supplied search string.

    Returns
    -------
    str
        A sanitized string safe for interpolation into PostgREST
        ``ilike`` filter expressions.
    """
    return _POSTGREST_UNSAFE_RE.sub("", value)
