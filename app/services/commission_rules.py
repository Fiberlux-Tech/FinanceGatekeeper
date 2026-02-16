"""
Commission Rules Engine.

Pure-function module containing all hard-coded commission calculation logic
for each business unit (ESTADO, GIGALAN, CORPORATIVO, MAYORISTA).

All financial values (total_revenue, MRC, etc.) are expected in PEN.
Functions are stateless: input data -> output result, no side effects.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.logger import StructuredLogger
from app.models.service_models import CommissionInput


def _calculate_estado_commission(
    data: CommissionInput,
    logger: Optional[StructuredLogger] = None,
) -> Decimal:
    """
    Calculate commission for the 'ESTADO' business unit.

    Applies tiered commission rates based on gross margin ratio,
    contract term (plazo), and payback period. Caps the result
    against a fixed PEN limit (pago unico) or an MRC multiplier
    (recurrent deals).
    """
    total_revenues: Decimal = data.total_revenue

    if total_revenues == 0:
        return Decimal("0")

    plazo: int = data.plazo_contrato
    payback: Optional[int] = data.payback
    mrc: Decimal = data.mrc_pen
    payback_ok: bool = payback is not None
    rentabilidad: Decimal = data.gross_margin_ratio

    final_commission_amount: Decimal = Decimal("0")
    commission_rate: Decimal = Decimal("0")

    # Pago Unico is defined as a contract term of 1 month or less.
    is_pago_unico: bool = plazo <= 1

    _STANDARD_PLAZOS: set[int] = {12, 24, 36, 48}

    if is_pago_unico:
        # PAGO UNICO LOGIC
        limit_pen: Decimal = Decimal("0")
        if Decimal("0.30") <= rentabilidad <= Decimal("0.35"):
            commission_rate, limit_pen = Decimal("0.01"), Decimal("11000")
        elif Decimal("0.35") < rentabilidad <= Decimal("0.39"):
            commission_rate, limit_pen = Decimal("0.02"), Decimal("12000")
        elif Decimal("0.39") < rentabilidad <= Decimal("0.49"):
            commission_rate, limit_pen = Decimal("0.03"), Decimal("13000")
        elif Decimal("0.49") < rentabilidad <= Decimal("0.59"):
            commission_rate, limit_pen = Decimal("0.04"), Decimal("14000")
        elif rentabilidad > Decimal("0.59"):
            commission_rate, limit_pen = Decimal("0.05"), Decimal("15000")
        elif rentabilidad < Decimal("0.30") and logger is not None:
            logger.warning(
                "ESTADO pago unico: margin %.4f < 0.30 — no commission tier applies",
                rentabilidad,
            )

        if commission_rate > 0:
            calculated_commission: Decimal = total_revenues * commission_rate
            final_commission_amount = min(calculated_commission, limit_pen)
    else:
        # RECURRENT DEAL LOGIC (Plazo dependent)
        limit_mrc_multiplier: Decimal = Decimal("0")

        if plazo == 12:
            if Decimal("0.30") <= rentabilidad <= Decimal("0.35") and payback_ok and payback <= 7:
                commission_rate, limit_mrc_multiplier = Decimal("0.025"), Decimal("0.8")
            elif Decimal("0.35") < rentabilidad <= Decimal("0.39") and payback_ok and payback <= 7:
                commission_rate, limit_mrc_multiplier = Decimal("0.03"), Decimal("0.9")
            elif rentabilidad > Decimal("0.39") and payback_ok and payback <= 6:
                commission_rate, limit_mrc_multiplier = Decimal("0.035"), Decimal("1")
        elif plazo == 24:
            if Decimal("0.30") <= rentabilidad <= Decimal("0.35") and payback_ok and payback <= 11:
                commission_rate, limit_mrc_multiplier = Decimal("0.025"), Decimal("0.8")
            elif Decimal("0.35") < rentabilidad <= Decimal("0.39") and payback_ok and payback <= 11:
                commission_rate, limit_mrc_multiplier = Decimal("0.03"), Decimal("0.9")
            elif rentabilidad > Decimal("0.39") and payback_ok and payback <= 10:
                commission_rate, limit_mrc_multiplier = Decimal("0.035"), Decimal("1")
        elif plazo == 36:
            if Decimal("0.30") <= rentabilidad <= Decimal("0.35") and payback_ok and payback <= 19:
                commission_rate, limit_mrc_multiplier = Decimal("0.025"), Decimal("0.8")
            elif Decimal("0.35") < rentabilidad <= Decimal("0.39") and payback_ok and payback <= 19:
                commission_rate, limit_mrc_multiplier = Decimal("0.03"), Decimal("0.9")
            elif rentabilidad > Decimal("0.39") and payback_ok and payback <= 18:
                commission_rate, limit_mrc_multiplier = Decimal("0.035"), Decimal("1")
        elif plazo == 48:
            if Decimal("0.30") <= rentabilidad <= Decimal("0.35") and payback_ok and payback <= 26:
                commission_rate, limit_mrc_multiplier = Decimal("0.02"), Decimal("0.8")
            elif Decimal("0.35") < rentabilidad <= Decimal("0.39") and payback_ok and payback <= 26:
                commission_rate, limit_mrc_multiplier = Decimal("0.025"), Decimal("0.9")
            elif rentabilidad > Decimal("0.39") and payback_ok and payback <= 25:
                commission_rate, limit_mrc_multiplier = Decimal("0.03"), Decimal("1")

        if commission_rate == Decimal("0") and logger is not None:
            if plazo not in _STANDARD_PLAZOS and plazo > 1:
                logger.warning(
                    "ESTADO recurrent: non-standard plazo %d months "
                    "(supported: 12, 24, 36, 48) — commission is 0.0",
                    plazo,
                )
            elif rentabilidad < Decimal("0.30"):
                logger.warning(
                    "ESTADO recurrent: margin %.4f < 0.30 for plazo %d "
                    "— no commission tier applies",
                    rentabilidad,
                    plazo,
                )

        if commission_rate > Decimal("0"):
            calculated_commission = total_revenues * commission_rate
            limit_mrc_amount: Decimal = mrc * limit_mrc_multiplier
            final_commission_amount = min(calculated_commission, limit_mrc_amount)

    return final_commission_amount


def _calculate_gigalan_commission(
    data: CommissionInput,
    logger: Optional[StructuredLogger] = None,
) -> Decimal:
    """
    Calculate commission for the 'GIGALAN' business unit.

    Rates depend on region, sale type (NUEVO / EXISTENTE), and gross
    margin ratio. A payback >= 2 disqualifies the deal entirely.
    """
    region: Optional[str] = data.gigalan_region
    sale_type: Optional[str] = data.gigalan_sale_type

    # Use Decimal("0") if None or Decimal("0")
    old_mrc_pen: Decimal = data.gigalan_old_mrc if data.gigalan_old_mrc is not None else Decimal("0")

    payback: Optional[int] = data.payback
    rentabilidad: Decimal = data.gross_margin_ratio
    plazo: int = data.plazo_contrato
    mrc_pen: Decimal = data.mrc_pen

    commission_rate: Decimal = Decimal("0")
    calculated_commission: Decimal = Decimal("0")

    # Initial Validation (Handles incomplete GIGALAN inputs)
    if not region or not sale_type:
        return Decimal("0")

    # Payback Period Rule
    if payback is not None and payback >= 2:
        return Decimal("0")

    # FULL GIGALAN COMMISSION LOGIC
    if region == 'LIMA':
        if sale_type == 'NUEVO':
            if Decimal("0.40") <= rentabilidad < Decimal("0.50"):
                commission_rate = Decimal("0.009")
            elif Decimal("0.50") <= rentabilidad < Decimal("0.60"):
                commission_rate = Decimal("0.014")
            elif Decimal("0.60") <= rentabilidad < Decimal("0.70"):
                commission_rate = Decimal("0.019")
            elif rentabilidad >= Decimal("0.70"):
                commission_rate = Decimal("0.024")
        elif sale_type == 'EXISTENTE':
            if Decimal("0.40") <= rentabilidad < Decimal("0.50"):
                commission_rate = Decimal("0.01")
            elif Decimal("0.50") <= rentabilidad < Decimal("0.60"):
                commission_rate = Decimal("0.015")
            elif Decimal("0.60") <= rentabilidad < Decimal("0.70"):
                commission_rate = Decimal("0.02")
            elif rentabilidad >= Decimal("0.70"):
                commission_rate = Decimal("0.025")

    elif region == 'PROVINCIAS CON CACHING':
        if Decimal("0.40") <= rentabilidad < Decimal("0.45"):
            commission_rate = Decimal("0.03")
        elif rentabilidad >= Decimal("0.45"):
            commission_rate = Decimal("0.035")

    elif region == 'PROVINCIAS CON INTERNEXA':
        if Decimal("0.17") <= rentabilidad < Decimal("0.20"):
            commission_rate = Decimal("0.02")
        elif rentabilidad >= Decimal("0.20"):
            commission_rate = Decimal("0.03")

    elif region == 'PROVINCIAS CON TDP':
        if Decimal("0.17") <= rentabilidad < Decimal("0.20"):
            commission_rate = Decimal("0.02")
        elif rentabilidad >= Decimal("0.20"):
            commission_rate = Decimal("0.03")

    # FINAL CALCULATION (All PEN)
    if sale_type == 'NUEVO':
        calculated_commission = commission_rate * mrc_pen * plazo
    elif sale_type == 'EXISTENTE':
        calculated_commission = commission_rate * plazo * (mrc_pen - old_mrc_pen)
    else:
        calculated_commission = Decimal("0")

    return calculated_commission


def _calculate_corporativo_commission(
    data: CommissionInput,
    logger: Optional[StructuredLogger] = None,
) -> Decimal:
    """CORPORATIVO commission: awaiting business rules definition.

    Returns ``Decimal("0")`` for all deals until the Finance team provides
    the rate tables, margin thresholds, and cap structures for the
    CORPORATIVO business unit.

    Once rules are defined, this function should follow the same
    pattern as ``_calculate_estado_commission``: tiered rates based
    on ``gross_margin_ratio`` and ``plazo_contrato``, with caps
    against either a fixed PEN limit or an MRC multiplier.

    Parameters
    ----------
    data:
        Validated commission input.
    logger:
        When provided, emits a warning that rules are pending.
    """
    if logger is not None:
        logger.warning(
            "CORPORATIVO commission rules not yet defined — returning 0.0 "
            "for deal with revenue %.2f PEN, margin %.4f",
            data.total_revenue,
            data.gross_margin_ratio,
        )
    return Decimal("0")


def _calculate_mayorista_commission(
    data: CommissionInput,
    logger: Optional[StructuredLogger] = None,
) -> Decimal:
    """MAYORISTA commission: awaiting business rules definition.

    Returns ``Decimal("0")`` for all deals until the Finance team provides
    the rate tables, margin thresholds, and cap structures for the
    MAYORISTA business unit.

    Parameters
    ----------
    data:
        Validated commission input.
    logger:
        When provided, emits a warning that rules are pending.
    """
    if logger is not None:
        logger.warning(
            "MAYORISTA commission rules not yet defined — returning 0.0 "
            "for deal with revenue %.2f PEN, margin %.4f",
            data.total_revenue,
            data.gross_margin_ratio,
        )
    return Decimal("0")


def calculate_commission(
    data: CommissionInput,
    logger: Optional[StructuredLogger] = None,
) -> Decimal:
    """
    Route commission calculation to the appropriate business unit handler.

    This is the public entry point for all commission calculations.

    Args:
        data: Validated commission input containing financial metrics and
              business unit identifier.
        logger: Optional ``StructuredLogger`` instance. When provided,
                warnings are emitted for unrecognized business units,
                low-margin ESTADO deals, and non-standard plazos.

    Returns:
        The calculated commission amount in PEN.
    """
    unit: str = data.unidad_negocio

    if unit == 'ESTADO':
        return _calculate_estado_commission(data, logger=logger)
    elif unit == 'GIGALAN':
        return _calculate_gigalan_commission(data, logger=logger)
    elif unit == 'CORPORATIVO':
        return _calculate_corporativo_commission(data, logger=logger)
    elif unit == 'MAYORISTA':
        return _calculate_mayorista_commission(data, logger=logger)
    else:
        if logger is not None:
            logger.warning(
                "Unrecognized business unit '%s' — returning 0.0 commission",
                unit,
            )
        return Decimal("0")
