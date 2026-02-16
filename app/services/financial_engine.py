"""
Modular Financial Engine.

Decomposed from ``_calculate_financial_metrics`` into discrete, testable
components.  This module is a pure logic library with **no** imports from
``transactions.py``.  Pure Math: input data -> output result, no side effects.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

from app.models.enums import Currency
from app.models.service_models import (
    CommissionInput,
    FinancialEngineInput,
    FinancialMetricsResult,
    FixedCostInput,
    KPIResult,
    RecurringServiceInput,
)
from app.services.commission_rules import calculate_commission
from app.utils.math_utils import calculate_irr, calculate_npv

__all__ = [
    "CurrencyConverter",
    "build_timeline",
    "calculate_carta_fianza",
    "calculate_financial_metrics",
    "calculate_kpis",
    "initialize_timeline",
    "process_fixed_costs",
    "process_recurring_services",
    "resolve_mrc",
]

# ---------------------------------------------------------------------------
# Timeline type aliases — kept as TypeAliases because the timeline is a
# deeply nested dict structure that serves as output only.
# ---------------------------------------------------------------------------
_TimelineLeaf = Union[Decimal, int, str, None]
_TimelineValue = Union[
    _TimelineLeaf,
    list[_TimelineLeaf],
    dict[str, Union[_TimelineLeaf, list[_TimelineLeaf]]],
    list[dict[str, Union[_TimelineLeaf, list[_TimelineLeaf]]]],
]
TimelineDict = dict[str, _TimelineValue]


# --- 1. CurrencyConverter ---

class CurrencyConverter:
    """Holds exchange rate state and converts values to PEN."""

    tipo_cambio: Decimal

    def __init__(self, tipo_cambio: Decimal = Decimal("1")) -> None:
        self.tipo_cambio: Decimal = tipo_cambio or Decimal("1")

    def to_pen(self, value: Decimal, currency: Union[Currency, str]) -> Decimal:
        """Convert a monetary value to PEN using the stored exchange rate.

        Args:
            value: The monetary amount (defaults to Decimal("0") if falsy).
            currency: The source currency (PEN or USD).

        Returns:
            The equivalent value in PEN.
        """
        value = value or Decimal("0")
        if currency == Currency.USD:
            return value * self.tipo_cambio
        return value


# --- 2. RecurringServiceProcessor ---

def process_recurring_services(
    services: list[RecurringServiceInput],
    converter: CurrencyConverter,
) -> tuple[list[RecurringServiceInput], Decimal, Decimal]:
    """Enrich each service with PEN fields and return aggregates.

    Returns new ``RecurringServiceInput`` instances with computed PEN
    fields populated.  The original input models are not modified.

    Args:
        services: List of recurring service models to enrich.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (enriched_services, total_monthly_expense_pen,
        mrc_sum_from_services_orig).
    """
    mrc_sum_orig: Decimal = Decimal("0")
    total_monthly_expense_pen: Decimal = Decimal("0")
    enriched: list[RecurringServiceInput] = []

    for item in services:
        q: int = item.quantity or 0

        p_original: Decimal = item.price_original or Decimal("0")
        p_currency: Currency = item.price_currency
        p_pen: Decimal = converter.to_pen(p_original, p_currency)
        ingreso_pen: Decimal = p_pen * q
        mrc_sum_orig += p_original * q

        cu1_original: Decimal = item.cost_unit_1_original or Decimal("0")
        cu2_original: Decimal = item.cost_unit_2_original or Decimal("0")
        cu_currency: Currency = item.cost_unit_currency
        cu1_pen: Decimal = converter.to_pen(cu1_original, cu_currency)
        cu2_pen: Decimal = converter.to_pen(cu2_original, cu_currency)
        egreso_pen: Decimal = (cu1_pen + cu2_pen) * q
        total_monthly_expense_pen += egreso_pen

        enriched.append(item.model_copy(update={
            "price_pen": p_pen,
            "ingreso_pen": ingreso_pen,
            "cost_unit_1_pen": cu1_pen,
            "cost_unit_2_pen": cu2_pen,
            "egreso_pen": egreso_pen,
        }))

    return enriched, total_monthly_expense_pen, mrc_sum_orig


# --- 3. MRCResolver ---

def resolve_mrc(
    user_provided_mrc_original: Decimal,
    mrc_sum_from_services_orig: Decimal,
    mrc_currency: Union[Currency, str],
    converter: CurrencyConverter,
) -> tuple[Decimal, Decimal]:
    """Determine final MRC using override logic.

    If the user provided a positive MRC value, it takes precedence over the
    sum computed from individual services.

    Args:
        user_provided_mrc_original: User-supplied MRC override (may be 0).
        mrc_sum_from_services_orig: Sum of service-level MRC values.
        mrc_currency: Currency of the MRC values.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (final_mrc_original, final_mrc_pen).
    """
    user_provided: Decimal = user_provided_mrc_original or Decimal("0")
    if user_provided > 0:
        final_mrc_original: Decimal = user_provided
    else:
        final_mrc_original = mrc_sum_from_services_orig

    final_mrc_pen: Decimal = converter.to_pen(final_mrc_original, mrc_currency)
    return final_mrc_original, final_mrc_pen


# --- 4. FixedCostProcessor ---

def process_fixed_costs(
    fixed_costs: list[FixedCostInput],
    converter: CurrencyConverter,
) -> tuple[list[FixedCostInput], Decimal]:
    """Normalize fixed costs to PEN and calculate the total.

    Returns new ``FixedCostInput`` instances with computed PEN fields
    populated.  The original input models are not modified.

    Args:
        fixed_costs: List of fixed cost models to enrich.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (enriched_costs, total_installation_pen).
    """
    total_installation_pen: Decimal = Decimal("0")
    enriched: list[FixedCostInput] = []

    for item in fixed_costs:
        cantidad: int = item.cantidad or 0
        costo_unitario_original: Decimal = item.costo_unitario_original or Decimal("0")
        costo_unitario_currency: Currency = item.costo_unitario_currency
        costo_unitario_pen: Decimal = converter.to_pen(costo_unitario_original, costo_unitario_currency)
        total_pen: Decimal = cantidad * costo_unitario_pen
        total_installation_pen += total_pen

        enriched.append(item.model_copy(update={
            "costo_unitario_pen": costo_unitario_pen,
            "total_pen": total_pen,
        }))

    return enriched, total_installation_pen


# --- 5. CartaFianzaCalculator ---

def calculate_carta_fianza(
    aplica: bool,
    tasa: Decimal,
    plazo: int,
    mrc_original: Decimal,
    mrc_currency: Union[Currency, str],
    converter: CurrencyConverter,
) -> tuple[Decimal, Decimal]:
    """Calculate Carta Fianza cost in original currency and PEN.

    Formula: 10% * plazo * MRC_ORIG * 1.18 * tasa

    Args:
        aplica: Whether Carta Fianza applies to this deal.
        tasa: The Carta Fianza interest rate.
        plazo: Contract term in months.
        mrc_original: Monthly recurring charge in original currency.
        mrc_currency: Currency of the MRC.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (costo_orig, costo_pen). Both are Decimal("0") when not applicable.
    """
    if not aplica:
        return Decimal("0"), Decimal("0")

    tasa = tasa or Decimal("0")
    costo_orig: Decimal = Decimal("0.10") * plazo * mrc_original * Decimal("1.18") * tasa
    costo_pen: Decimal = converter.to_pen(costo_orig, mrc_currency)
    return costo_orig, costo_pen


# --- 6. CommissionCoordinator ---

def _prepare_and_calculate_commission(
    unidad_negocio: str,
    plazo_contrato: int,
    total_revenue: Decimal,
    gross_margin_ratio: Decimal,
    mrc_pen: Decimal,
    payback: Optional[int],
    gigalan_region: Optional[str],
    gigalan_sale_type: Optional[str],
    gigalan_old_mrc: Optional[Decimal],
) -> Decimal:
    """Construct a ``CommissionInput`` from explicit params and delegate to the rules engine.

    Args:
        unidad_negocio: Business unit code.
        plazo_contrato: Contract term in months.
        total_revenue: Total deal revenue in PEN.
        gross_margin_ratio: Gross margin as a ratio (0.0 -- 1.0).
        mrc_pen: Monthly recurring charge in PEN.
        payback: Payback period (months), or None if not computed.
        gigalan_region: GIGALAN region (optional).
        gigalan_sale_type: GIGALAN sale type (optional).
        gigalan_old_mrc: GIGALAN old MRC for upsell calculations (optional).

    Returns:
        The calculated commission amount in PEN.
    """
    commission_input: CommissionInput = CommissionInput(
        unidad_negocio=unidad_negocio,
        total_revenue=total_revenue,
        mrc_pen=mrc_pen,
        plazo_contrato=plazo_contrato,
        payback=payback,
        gross_margin_ratio=gross_margin_ratio,
        gigalan_region=gigalan_region,
        gigalan_sale_type=gigalan_sale_type,
        gigalan_old_mrc=gigalan_old_mrc,
    )

    return calculate_commission(commission_input)


# --- 7. TimelineGenerator ---

def initialize_timeline(num_periods: int) -> TimelineDict:
    """Create a dictionary to hold the detailed timeline components.

    Args:
        num_periods: Number of periods (months) in the timeline.

    Returns:
        A nested dict skeleton with zeroed revenue, expense, and net cash
        flow arrays.
    """
    return {
        'periods': [f"t={i}" for i in range(num_periods)],
        'revenues': {
            'nrc': [Decimal("0")] * num_periods,
            'mrc': [Decimal("0")] * num_periods,
        },
        'expenses': {
            'comisiones': [Decimal("0")] * num_periods,
            'egreso': [Decimal("0")] * num_periods,
            'fixed_costs': [],
        },
        'net_cash_flow': [Decimal("0")] * num_periods,
    }


def build_timeline(
    num_periods: int,
    nrc_pen: Decimal,
    mrc_pen: Decimal,
    comisiones: Decimal,
    carta_fianza_pen: Decimal,
    monthly_expense_pen: Decimal,
    fixed_costs: list[FixedCostInput],
) -> tuple[TimelineDict, Decimal, list[Decimal]]:
    """Build period-by-period cash flow timeline.

    Args:
        num_periods: Total number of periods (plazo + 1, includes t=0).
        nrc_pen: Non-recurring charge in PEN (applied at t=0).
        mrc_pen: Monthly recurring charge in PEN (applied at t=1..N).
        comisiones: Commission amount in PEN (expensed at t=0).
        carta_fianza_pen: Carta Fianza cost in PEN (expensed at t=0).
        monthly_expense_pen: Recurring monthly expense in PEN.
        fixed_costs: List of fixed cost models with distribution params.

    Returns:
        Tuple of (timeline_dict, total_fixed_costs_applied_pen,
        net_cash_flow_list).
    """
    timeline: TimelineDict = initialize_timeline(num_periods)

    # A. Revenues
    timeline['revenues']['nrc'][0] = nrc_pen
    for i in range(1, num_periods):
        timeline['revenues']['mrc'][i] = mrc_pen

    # B. Expenses
    timeline['expenses']['comisiones'][0] = -comisiones - carta_fianza_pen
    for i in range(1, num_periods):
        timeline['expenses']['egreso'][i] = -monthly_expense_pen

    # C. Fixed costs distribution
    total_fixed_costs_applied_pen: Decimal = Decimal("0")
    for cost_item in fixed_costs:
        cost_total_pen: Decimal = cost_item.total_pen or Decimal("0")
        periodo_inicio: int = cost_item.periodo_inicio or 0
        duracion_meses: int = max(cost_item.duracion_meses or 1, 1)

        cost_timeline_values: list[Decimal] = [Decimal("0")] * num_periods
        distributed_cost: Decimal = cost_total_pen / duracion_meses

        for i in range(duracion_meses):
            current_period: int = periodo_inicio + i
            if current_period < num_periods:
                cost_timeline_values[current_period] = -distributed_cost
                total_fixed_costs_applied_pen += distributed_cost

        timeline['expenses']['fixed_costs'].append({
            "id": cost_item.id,
            "categoria": cost_item.categoria,
            "tipo_servicio": cost_item.tipo_servicio,
            "total": cost_total_pen,
            "periodo_inicio": periodo_inicio,
            "duracion_meses": duracion_meses,
            "timeline_values": cost_timeline_values,
        })

    # D. Net cash flow
    net_cash_flow_list: list[Decimal] = []
    for t in range(num_periods):
        net_t: Decimal = (
            timeline['revenues']['nrc'][t]
            + timeline['revenues']['mrc'][t]
            + timeline['expenses']['comisiones'][t]
            + timeline['expenses']['egreso'][t]
        )
        for fc in timeline['expenses']['fixed_costs']:
            net_t += fc['timeline_values'][t]

        timeline['net_cash_flow'][t] = net_t
        net_cash_flow_list.append(net_t)

    return timeline, total_fixed_costs_applied_pen, net_cash_flow_list


# --- 8. KPICalculator ---

def calculate_kpis(
    net_cash_flow_list: list[Decimal],
    total_revenue: Decimal,
    total_expense: Decimal,
    costo_capital_anual: Decimal,
) -> KPIResult:
    """Calculate VAN, TIR, payback, gross margin, and gross margin ratio.

    Args:
        net_cash_flow_list: Period-by-period net cash flows.
        total_revenue: Total deal revenue in PEN.
        total_expense: Total deal expense in PEN.
        costo_capital_anual: Annual cost of capital (decimal, not percentage).

    Returns:
        ``KPIResult`` model with van, tir, payback, total_revenue,
        total_expense, gross_margin, and gross_margin_ratio.
    """
    monthly_discount_rate: Decimal = costo_capital_anual / 12
    van: Decimal = calculate_npv(monthly_discount_rate, net_cash_flow_list)
    tir: Optional[Decimal] = calculate_irr(net_cash_flow_list)

    cumulative_cash_flow: Decimal = Decimal("0")
    payback: Optional[int] = None
    for i, flow in enumerate(net_cash_flow_list):
        cumulative_cash_flow += flow
        if cumulative_cash_flow >= 0:
            payback = i
            break

    gross_margin: Decimal = total_revenue - total_expense

    return KPIResult(
        van=van,
        tir=tir,
        payback=payback,
        total_revenue=total_revenue,
        total_expense=total_expense,
        gross_margin=gross_margin,
        gross_margin_ratio=(gross_margin / total_revenue) if total_revenue else Decimal("0"),
    )


# --- 9. Main Orchestrator ---

def calculate_financial_metrics(
    data: Union[FinancialEngineInput, dict[str, object]],
) -> FinancialMetricsResult:
    """Orchestrate all modular financial engine components.

    This is the main entry point for financial calculations. It coordinates
    currency conversion, service processing, cost resolution, commission
    computation, timeline generation, and KPI derivation.

    Accepts either a validated ``FinancialEngineInput`` model or a raw dict.
    When a dict is passed it is validated into the model automatically,
    preserving backward compatibility with callers that build dicts.

    Args:
        data: ``FinancialEngineInput`` model (preferred) or a dict with keys:
            - tipo_cambio: Exchange rate (USD to PEN)
            - plazo_contrato: Contract term in months
            - recurring_services: List of recurring service items
            - mrc_original, mrc_currency: Monthly Recurring Charge
            - nrc_original, nrc_currency: Non-Recurring Charge
            - fixed_costs: List of fixed cost items
            - aplica_carta_fianza, tasa_carta_fianza: Carta Fianza settings
            - costo_capital_anual: Annual cost of capital for NPV calculation

    Returns:
        ``FinancialMetricsResult`` model with calculated financial metrics
        including van, tir, timeline, commissions, and margin ratios.
    """
    # SAFETY: Deep-copy to isolate the caller's input from engine
    # internals.  Sub-functions return new model instances (no in-place
    # mutation), but the copy still prevents accidental coupling if the
    # caller later inspects or re-uses the original FinancialEngineInput.
    if isinstance(data, dict):
        # Validate raw dict into the model (deep copy is implicit in model creation)
        engine_input: FinancialEngineInput = FinancialEngineInput.model_validate(data)
    else:
        engine_input = data.model_copy(deep=True)

    # --- Guard clauses: reject nonsensical inputs early (M3) ---
    if engine_input.plazo_contrato < 0:
        raise ValueError(
            f"plazo_contrato must be >= 0, got {engine_input.plazo_contrato}"
        )
    if engine_input.tipo_cambio <= 0:
        raise ValueError(
            f"tipo_cambio must be > 0, got {engine_input.tipo_cambio}"
        )
    if engine_input.costo_capital_anual < 0 or engine_input.costo_capital_anual > Decimal("10"):
        raise ValueError(
            f"costo_capital_anual must be between 0 and 10.0 (1000%), "
            f"got {engine_input.costo_capital_anual}"
        )

    converter: CurrencyConverter = CurrencyConverter(engine_input.tipo_cambio)
    plazo: int = engine_input.plazo_contrato

    # 1. Process recurring services
    services: list[RecurringServiceInput]
    monthly_expense_pen: Decimal
    mrc_sum_orig: Decimal
    services, monthly_expense_pen, mrc_sum_orig = process_recurring_services(
        engine_input.recurring_services, converter,
    )

    # 2. Resolve MRC (override vs. sum from services)
    mrc_orig: Decimal
    mrc_pen: Decimal
    mrc_orig, mrc_pen = resolve_mrc(
        engine_input.mrc_original,
        mrc_sum_orig,
        engine_input.mrc_currency,
        converter,
    )

    # 3. NRC normalization
    nrc_orig: Decimal = engine_input.nrc_original or Decimal("0")
    nrc_pen: Decimal = converter.to_pen(nrc_orig, engine_input.nrc_currency)

    # 4. Fixed costs
    costs: list[FixedCostInput]
    installation_pen: Decimal
    costs, installation_pen = process_fixed_costs(
        engine_input.fixed_costs, converter,
    )

    # 5. Carta Fianza
    cf_orig: Decimal
    cf_pen: Decimal
    cf_orig, cf_pen = calculate_carta_fianza(
        engine_input.aplica_carta_fianza,
        engine_input.tasa_carta_fianza,
        plazo,
        mrc_orig,
        engine_input.mrc_currency,
        converter,
    )

    # 6. Revenue & pre-commission margin
    total_revenue: Decimal = nrc_pen + (mrc_pen * plazo)
    total_expense_pre: Decimal = installation_pen + (monthly_expense_pen * plazo)
    gm_pre: Decimal = total_revenue - total_expense_pre
    gm_ratio: Decimal = (gm_pre / total_revenue) if total_revenue else Decimal("0")

    # 7. Commission
    comisiones: Decimal = _prepare_and_calculate_commission(
        unidad_negocio=engine_input.unidad_negocio,
        plazo_contrato=plazo,
        total_revenue=total_revenue,
        gross_margin_ratio=gm_ratio,
        mrc_pen=mrc_pen,
        payback=None,
        gigalan_region=engine_input.gigalan_region,
        gigalan_sale_type=engine_input.gigalan_sale_type,
        gigalan_old_mrc=engine_input.gigalan_old_mrc,
    )

    # 8. Timeline
    timeline: TimelineDict
    fixed_applied: Decimal
    ncf_list: list[Decimal]
    timeline, fixed_applied, ncf_list = build_timeline(
        plazo + 1, nrc_pen, mrc_pen, comisiones, cf_pen,
        monthly_expense_pen, engine_input.fixed_costs,
    )

    # 9. KPIs
    total_expense: Decimal = comisiones + fixed_applied + (monthly_expense_pen * plazo) + cf_pen
    kpis: KPIResult = calculate_kpis(
        ncf_list, total_revenue, total_expense, engine_input.costo_capital_anual,
    )

    return FinancialMetricsResult(
        mrc_original=mrc_orig,
        mrc_pen=mrc_pen,
        nrc_original=nrc_orig,
        nrc_pen=nrc_pen,
        van=kpis.van,
        tir=kpis.tir,
        payback=kpis.payback,
        total_revenue=kpis.total_revenue,
        total_expense=kpis.total_expense,
        gross_margin=kpis.gross_margin,
        gross_margin_ratio=kpis.gross_margin_ratio,
        comisiones=comisiones,
        comisiones_rate=(comisiones / total_revenue) if total_revenue else Decimal("0"),
        costo_instalacion=fixed_applied,
        costo_instalacion_ratio=(fixed_applied / total_revenue) if total_revenue else Decimal("0"),
        costo_carta_fianza=cf_pen,
        aplica_carta_fianza=engine_input.aplica_carta_fianza,
        timeline=timeline,
    )
