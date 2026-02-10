"""
Commission Rules Engine.

Pure-function module containing all hard-coded commission calculation logic
for each business unit (ESTADO, GIGALAN, CORPORATIVO).

All financial values (total_revenue, MRC, etc.) are expected in PEN.
Functions are stateless: input data -> output result, no side effects.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.models.service_models import CommissionInput


def _calculate_estado_commission(data: CommissionInput) -> float:
    """
    Calculate commission for the 'ESTADO' business unit.

    Applies tiered commission rates based on gross margin ratio,
    contract term (plazo), and payback period. Caps the result
    against a fixed PEN limit (pago unico) or an MRC multiplier
    (recurrent deals).
    """
    total_revenues: float = data.total_revenue

    if total_revenues == 0:
        return 0.0

    plazo: int = data.plazo_contrato
    payback: Optional[int] = data.payback
    mrc: float = data.mrc_pen
    payback_ok: bool = payback is not None
    rentabilidad: float = data.gross_margin_ratio

    final_commission_amount: float = 0.0
    commission_rate: float = 0.0

    # Pago Unico is defined as a contract term of 1 month or less.
    is_pago_unico: bool = plazo <= 1

    if is_pago_unico:
        # PAGO UNICO LOGIC
        limit_pen: float = 0.0
        if 0.30 <= rentabilidad <= 0.35:
            commission_rate, limit_pen = 0.01, 11000
        elif 0.35 < rentabilidad <= 0.39:
            commission_rate, limit_pen = 0.02, 12000
        elif 0.39 < rentabilidad <= 0.49:
            commission_rate, limit_pen = 0.03, 13000
        elif 0.49 < rentabilidad <= 0.59:
            commission_rate, limit_pen = 0.04, 14000
        elif rentabilidad > 0.59:
            commission_rate, limit_pen = 0.05, 15000

        if commission_rate > 0:
            calculated_commission: float = total_revenues * commission_rate
            final_commission_amount = min(calculated_commission, limit_pen)
    else:
        # RECURRENT DEAL LOGIC (Plazo dependent)
        limit_mrc_multiplier: float = 0.0

        if plazo == 12:
            if 0.30 <= rentabilidad <= 0.35 and payback_ok and payback <= 7:
                commission_rate, limit_mrc_multiplier = 0.025, 0.8
            elif 0.35 < rentabilidad <= 0.39 and payback_ok and payback <= 7:
                commission_rate, limit_mrc_multiplier = 0.03, 0.9
            elif rentabilidad > 0.39 and payback_ok and payback <= 6:
                commission_rate, limit_mrc_multiplier = 0.035, 1.0
        elif plazo == 24:
            if 0.30 <= rentabilidad <= 0.35 and payback_ok and payback <= 11:
                commission_rate, limit_mrc_multiplier = 0.025, 0.8
            elif 0.35 < rentabilidad <= 0.39 and payback_ok and payback <= 11:
                commission_rate, limit_mrc_multiplier = 0.03, 0.9
            elif rentabilidad > 0.39 and payback_ok and payback <= 10:
                commission_rate, limit_mrc_multiplier = 0.035, 1.0
        elif plazo == 36:
            if 0.30 <= rentabilidad <= 0.35 and payback_ok and payback <= 19:
                commission_rate, limit_mrc_multiplier = 0.025, 0.8
            elif 0.35 < rentabilidad <= 0.39 and payback_ok and payback <= 19:
                commission_rate, limit_mrc_multiplier = 0.03, 0.9
            elif rentabilidad > 0.39 and payback_ok and payback <= 18:
                commission_rate, limit_mrc_multiplier = 0.035, 1.0
        elif plazo == 48:
            if 0.30 <= rentabilidad <= 0.35 and payback_ok and payback <= 26:
                commission_rate, limit_mrc_multiplier = 0.02, 0.8
            elif 0.35 < rentabilidad <= 0.39 and payback_ok and payback <= 26:
                commission_rate, limit_mrc_multiplier = 0.025, 0.9
            elif rentabilidad > 0.39 and payback_ok and payback <= 25:
                commission_rate, limit_mrc_multiplier = 0.03, 1.0

        # All other plazo values (e.g., 60 months) default to 0 commission rate

        if commission_rate > 0.0:
            calculated_commission = total_revenues * commission_rate
            limit_mrc_amount: float = mrc * limit_mrc_multiplier
            final_commission_amount = min(calculated_commission, limit_mrc_amount)

    return final_commission_amount


def _calculate_gigalan_commission(data: CommissionInput) -> float:
    """
    Calculate commission for the 'GIGALAN' business unit.

    Rates depend on region, sale type (NUEVO / EXISTENTE), and gross
    margin ratio. A payback >= 2 disqualifies the deal entirely.
    """
    region: Optional[str] = data.gigalan_region
    sale_type: Optional[str] = data.gigalan_sale_type

    # Use 0.0 if None or 0.0
    old_mrc_pen: float = data.gigalan_old_mrc if data.gigalan_old_mrc is not None else 0.0

    payback: Optional[int] = data.payback
    rentabilidad: float = data.gross_margin_ratio
    plazo: int = data.plazo_contrato
    mrc_pen: float = data.mrc_pen

    commission_rate: float = 0.0
    calculated_commission: float = 0.0

    # Initial Validation (Handles incomplete GIGALAN inputs)
    if not region or not sale_type:
        return 0.0

    # Payback Period Rule
    if payback is not None and payback >= 2:
        return 0.0

    # FULL GIGALAN COMMISSION LOGIC
    if region == 'LIMA':
        if sale_type == 'NUEVO':
            if 0.40 <= rentabilidad < 0.50:
                commission_rate = 0.009
            elif 0.50 <= rentabilidad < 0.60:
                commission_rate = 0.014
            elif 0.60 <= rentabilidad < 0.70:
                commission_rate = 0.019
            elif rentabilidad >= 0.70:
                commission_rate = 0.024
        elif sale_type == 'EXISTENTE':
            if 0.40 <= rentabilidad < 0.50:
                commission_rate = 0.01
            elif 0.50 <= rentabilidad < 0.60:
                commission_rate = 0.015
            elif 0.60 <= rentabilidad < 0.70:
                commission_rate = 0.02
            elif rentabilidad >= 0.70:
                commission_rate = 0.025

    elif region == 'PROVINCIAS CON CACHING':
        if 0.40 <= rentabilidad < 0.45:
            commission_rate = 0.03
        elif rentabilidad >= 0.45:
            commission_rate = 0.035

    elif region == 'PROVINCIAS CON INTERNEXA':
        if 0.17 <= rentabilidad < 0.20:
            commission_rate = 0.02
        elif rentabilidad >= 0.20:
            commission_rate = 0.03

    elif region == 'PROVINCIAS CON TDP':
        if 0.17 <= rentabilidad < 0.20:
            commission_rate = 0.02
        elif rentabilidad >= 0.20:
            commission_rate = 0.03

    # FINAL CALCULATION (All PEN)
    if sale_type == 'NUEVO':
        calculated_commission = commission_rate * mrc_pen * plazo
    elif sale_type == 'EXISTENTE':
        calculated_commission = commission_rate * plazo * (mrc_pen - old_mrc_pen)
    else:
        calculated_commission = 0.0

    return calculated_commission


def _calculate_corporativo_commission(data: CommissionInput) -> float:
    """
    Placeholder logic for 'CORPORATIVO' (no full rules defined yet).

    Currently caps at 1.2x MRC but always returns 0.0 because
    the calculated_commission base is 0.
    """
    mrc_pen: float = data.mrc_pen

    calculated_commission: float = 0
    limit_mrc_amount: float = 1.2 * mrc_pen

    return min(calculated_commission, limit_mrc_amount)


def calculate_commission(
    data: CommissionInput,
    logger: Optional[logging.Logger] = None,
) -> float:
    """
    Route commission calculation to the appropriate business unit handler.

    This is the public entry point for all commission calculations.

    Args:
        data: Validated commission input containing financial metrics and
              business unit identifier.
        logger: Optional logger instance. When provided, a warning is
                emitted if the business unit is unrecognized.

    Returns:
        The calculated commission amount in PEN.
    """
    unit: str = data.unidad_negocio

    if unit == 'ESTADO':
        return _calculate_estado_commission(data)
    elif unit == 'GIGALAN':
        return _calculate_gigalan_commission(data)
    elif unit == 'CORPORATIVO':
        return _calculate_corporativo_commission(data)
    else:
        if logger is not None:
            logger.warning(
                "Unrecognized business unit '%s' — returning 0.0 commission",
                unit,
            )
        return 0.0
