"""
Modular Financial Engine.

Decomposed from ``_calculate_financial_metrics`` into discrete, testable
components.  This module is a pure logic library with **no** imports from
``transactions.py``.  Pure Math: input data -> output result, no side effects.
"""

from __future__ import annotations

from typing import Optional, Union

from app.models.enums import Currency
from app.models.service_models import CommissionInput
from app.services.commission_rules import calculate_commission
from app.utils.math_utils import calculate_npv, calculate_irr

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
# Type aliases for complex dict structures used throughout this module.
# ---------------------------------------------------------------------------
ServiceItem = dict[str, Union[int, float, str, None]]
FixedCostItem = dict[str, Union[int, float, str, None]]

# Timeline dicts contain heterogeneous nested structures (lists of floats,
# dicts of lists, label strings).  We model the value side as a recursive
# Union that captures every concrete type stored inside.
_TimelineLeaf = Union[float, int, str, None]
_TimelineValue = Union[
    _TimelineLeaf,
    list[_TimelineLeaf],
    dict[str, Union[_TimelineLeaf, list[_TimelineLeaf]]],
    list[dict[str, Union[_TimelineLeaf, list[_TimelineLeaf]]]],
]
TimelineDict = dict[str, _TimelineValue]

KPIResult = dict[str, Union[float, int, None]]
FinancialMetricsResult = dict[str, Union[float, int, bool, None, TimelineDict]]

# Type alias for the heterogeneous mutable data dict passed through the
# orchestrator.  Captures every concrete type that callers store as values.
_DataValue = Union[
    int,
    float,
    str,
    bool,
    None,
    list[ServiceItem],
    list[FixedCostItem],
    Currency,
]
TransactionData = dict[str, _DataValue]


# --- 1. CurrencyConverter ---

class CurrencyConverter:
    """Holds exchange rate state and converts values to PEN."""

    tipo_cambio: float

    def __init__(self, tipo_cambio: float = 1.0) -> None:
        self.tipo_cambio: float = tipo_cambio or 1.0

    def to_pen(self, value: float, currency: Union[Currency, str]) -> float:
        """Convert a monetary value to PEN using the stored exchange rate.

        Args:
            value: The monetary amount (defaults to 0.0 if falsy).
            currency: The source currency (PEN or USD).

        Returns:
            The equivalent value in PEN.
        """
        value = value or 0.0
        if currency == Currency.USD:
            return value * self.tipo_cambio
        return value


# --- 2. RecurringServiceProcessor ---

def process_recurring_services(
    services: list[ServiceItem],
    converter: CurrencyConverter,
) -> tuple[list[ServiceItem], float, float]:
    """Enrich each service with PEN fields and return aggregates.

    Args:
        services: List of recurring service dicts to enrich in-place.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (enriched_services, total_monthly_expense_pen,
        mrc_sum_from_services_orig).
    """
    mrc_sum_orig: float = 0.0
    total_monthly_expense_pen: float = 0.0

    for item in services:
        q: int = item.get('quantity') or 0

        p_original: float = item.get('price_original') or 0.0
        p_currency: str = item.get('price_currency', Currency.PEN)
        p_pen: float = converter.to_pen(p_original, p_currency)
        item['price_pen'] = p_pen
        item['ingreso_pen'] = p_pen * q
        mrc_sum_orig += p_original * q

        cu1_original: float = item.get('cost_unit_1_original') or 0.0
        cu2_original: float = item.get('cost_unit_2_original') or 0.0
        cu_currency: str = item.get('cost_unit_currency', Currency.USD)
        cu1_pen: float = converter.to_pen(cu1_original, cu_currency)
        cu2_pen: float = converter.to_pen(cu2_original, cu_currency)
        item['cost_unit_1_pen'] = cu1_pen
        item['cost_unit_2_pen'] = cu2_pen
        item['egreso_pen'] = (cu1_pen + cu2_pen) * q
        total_monthly_expense_pen += item['egreso_pen']

    return services, total_monthly_expense_pen, mrc_sum_orig


# --- 3. MRCResolver ---

def resolve_mrc(
    user_provided_mrc_original: float,
    mrc_sum_from_services_orig: float,
    mrc_currency: Union[Currency, str],
    converter: CurrencyConverter,
) -> tuple[float, float]:
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
    user_provided: float = user_provided_mrc_original or 0.0
    if user_provided > 0:
        final_mrc_original: float = user_provided
    else:
        final_mrc_original = mrc_sum_from_services_orig

    final_mrc_pen: float = converter.to_pen(final_mrc_original, mrc_currency)
    return final_mrc_original, final_mrc_pen


# --- 4. FixedCostProcessor ---

def process_fixed_costs(
    fixed_costs: list[FixedCostItem],
    converter: CurrencyConverter,
) -> tuple[list[FixedCostItem], float]:
    """Normalize fixed costs to PEN and calculate the total.

    Args:
        fixed_costs: List of fixed cost dicts to enrich in-place.
        converter: Currency converter with the active exchange rate.

    Returns:
        Tuple of (enriched_costs, total_installation_pen).
    """
    total_installation_pen: float = 0.0

    for item in fixed_costs:
        cantidad: int = item.get('cantidad') or 0
        costo_unitario_original: float = item.get('costo_unitario_original') or 0.0
        costo_unitario_currency: str = item.get('costo_unitario_currency', Currency.USD)
        costo_unitario_pen: float = converter.to_pen(costo_unitario_original, costo_unitario_currency)
        item['costo_unitario_pen'] = costo_unitario_pen
        item['total_pen'] = cantidad * costo_unitario_pen
        total_installation_pen += item['total_pen']

    return fixed_costs, total_installation_pen


# --- 5. CartaFianzaCalculator ---

def calculate_carta_fianza(
    aplica: bool,
    tasa: float,
    plazo: int,
    mrc_original: float,
    mrc_currency: Union[Currency, str],
    converter: CurrencyConverter,
) -> tuple[float, float]:
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
        Tuple of (costo_orig, costo_pen). Both are 0.0 when not applicable.
    """
    if not aplica:
        return 0.0, 0.0

    tasa = tasa or 0.0
    costo_orig: float = 0.10 * plazo * mrc_original * 1.18 * tasa
    costo_pen: float = converter.to_pen(costo_orig, mrc_currency)
    return costo_orig, costo_pen


# --- 6. CommissionCoordinator ---

def _prepare_and_calculate_commission(
    data: TransactionData,
    total_revenue: float,
    gross_margin_pre: float,
    gross_margin_ratio: float,
    mrc_pen: float,
) -> float:
    """Prepare financial context on *data* and delegate to the commission rules engine.

    Injects pre-computed financial aggregates into the data dict so that the
    commission rules engine receives all the values it needs, then constructs
    a validated ``CommissionInput`` and calls ``calculate_commission``.

    Args:
        data: Mutable transaction data dict (enriched in-place with revenue
              and margin fields).
        total_revenue: Total deal revenue in PEN.
        gross_margin_pre: Gross margin before commission in PEN.
        gross_margin_ratio: Gross margin as a ratio (0.0 -- 1.0).
        mrc_pen: Monthly recurring charge in PEN.

    Returns:
        The calculated commission amount in PEN.
    """
    data['total_revenue'] = total_revenue
    data['gross_margin'] = gross_margin_pre
    data['gross_margin_ratio'] = gross_margin_ratio
    data['mrc_pen'] = mrc_pen

    commission_input: CommissionInput = CommissionInput(
        unidad_negocio=str(data.get('unidad_negocio', '')),
        total_revenue=total_revenue,
        mrc_pen=mrc_pen,
        plazo_contrato=int(data.get('plazo_contrato', 0)),
        payback=data.get('payback'),
        gross_margin_ratio=gross_margin_ratio,
        gigalan_region=data.get('gigalan_region'),
        gigalan_sale_type=data.get('gigalan_sale_type'),
        gigalan_old_mrc=data.get('gigalan_old_mrc'),
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
            'nrc': [0.0] * num_periods,
            'mrc': [0.0] * num_periods,
        },
        'expenses': {
            'comisiones': [0.0] * num_periods,
            'egreso': [0.0] * num_periods,
            'fixed_costs': [],
        },
        'net_cash_flow': [0.0] * num_periods,
    }


def build_timeline(
    num_periods: int,
    nrc_pen: float,
    mrc_pen: float,
    comisiones: float,
    carta_fianza_pen: float,
    monthly_expense_pen: float,
    fixed_costs: list[FixedCostItem],
) -> tuple[TimelineDict, float, list[float]]:
    """Build period-by-period cash flow timeline.

    Args:
        num_periods: Total number of periods (plazo + 1, includes t=0).
        nrc_pen: Non-recurring charge in PEN (applied at t=0).
        mrc_pen: Monthly recurring charge in PEN (applied at t=1..N).
        comisiones: Commission amount in PEN (expensed at t=0).
        carta_fianza_pen: Carta Fianza cost in PEN (expensed at t=0).
        monthly_expense_pen: Recurring monthly expense in PEN.
        fixed_costs: List of fixed cost items with distribution params.

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
    total_fixed_costs_applied_pen: float = 0.0
    for cost_item in fixed_costs:
        cost_total_pen: float = cost_item.get('total_pen', 0.0)
        periodo_inicio: int = int(cost_item.get('periodo_inicio', 0) or 0)
        duracion_meses: int = int(cost_item.get('duracion_meses', 1) or 1)

        cost_timeline_values: list[float] = [0.0] * num_periods
        distributed_cost: float = cost_total_pen / duracion_meses

        for i in range(duracion_meses):
            current_period: int = periodo_inicio + i
            if current_period < num_periods:
                cost_timeline_values[current_period] = -distributed_cost
                total_fixed_costs_applied_pen += distributed_cost

        timeline['expenses']['fixed_costs'].append({
            "id": cost_item.get('id'),
            "categoria": cost_item.get('categoria'),
            "tipo_servicio": cost_item.get('tipo_servicio'),
            "total": cost_total_pen,
            "periodo_inicio": periodo_inicio,
            "duracion_meses": duracion_meses,
            "timeline_values": cost_timeline_values,
        })

    # D. Net cash flow
    net_cash_flow_list: list[float] = []
    for t in range(num_periods):
        net_t: float = (
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
    net_cash_flow_list: list[float],
    total_revenue: float,
    total_expense: float,
    costo_capital_anual: float,
) -> KPIResult:
    """Calculate VAN, TIR, payback, gross margin, and gross margin ratio.

    Args:
        net_cash_flow_list: Period-by-period net cash flows.
        total_revenue: Total deal revenue in PEN.
        total_expense: Total deal expense in PEN.
        costo_capital_anual: Annual cost of capital (decimal, not percentage).

    Returns:
        Dict with keys: van, tir, payback, total_revenue, total_expense,
        gross_margin, gross_margin_ratio.
    """
    monthly_discount_rate: float = costo_capital_anual / 12
    van: float = calculate_npv(monthly_discount_rate, net_cash_flow_list)
    tir: Optional[float] = calculate_irr(net_cash_flow_list)

    cumulative_cash_flow: float = 0.0
    payback: Optional[int] = None
    for i, flow in enumerate(net_cash_flow_list):
        cumulative_cash_flow += flow
        if cumulative_cash_flow >= 0:
            payback = i
            break

    gross_margin: float = total_revenue - total_expense

    return {
        'van': van,
        'tir': tir,
        'payback': payback,
        'total_revenue': total_revenue,
        'total_expense': total_expense,
        'gross_margin': gross_margin,
        'gross_margin_ratio': (gross_margin / total_revenue) if total_revenue else 0.0,
    }


# --- 9. Main Orchestrator ---

def calculate_financial_metrics(data: TransactionData) -> FinancialMetricsResult:
    """Orchestrate all modular financial engine components.

    This is the main entry point for financial calculations. It coordinates
    currency conversion, service processing, cost resolution, commission
    computation, timeline generation, and KPI derivation.

    Args:
        data: Dictionary containing transaction data with keys:
            - tipo_cambio: Exchange rate (USD to PEN)
            - plazo_contrato: Contract term in months
            - recurring_services: List of recurring service items
            - mrc_original, mrc_currency: Monthly Recurring Charge
            - nrc_original, nrc_currency: Non-Recurring Charge
            - fixed_costs: List of fixed cost items
            - aplica_carta_fianza, tasa_carta_fianza: Carta Fianza settings
            - costo_capital_anual: Annual cost of capital for NPV calculation

    Returns:
        Dictionary with calculated financial metrics including van, tir,
        timeline, commissions, and margin ratios.
    """
    converter: CurrencyConverter = CurrencyConverter(data.get('tipo_cambio', 1))
    plazo: int = int(data.get('plazo_contrato', 0))

    # 1. Process recurring services
    services: list[ServiceItem]
    monthly_expense_pen: float
    mrc_sum_orig: float
    services, monthly_expense_pen, mrc_sum_orig = process_recurring_services(
        data.get('recurring_services', []), converter,
    )

    # 2. Resolve MRC (override vs. sum from services)
    mrc_orig: float
    mrc_pen: float
    mrc_orig, mrc_pen = resolve_mrc(
        data.get('mrc_original', 0.0),
        mrc_sum_orig,
        data.get('mrc_currency', Currency.PEN),
        converter,
    )

    # 3. NRC normalization
    nrc_orig: float = data.get('nrc_original', 0.0) or 0.0
    nrc_pen: float = converter.to_pen(nrc_orig, data.get('nrc_currency', Currency.PEN))

    # 4. Fixed costs
    costs: list[FixedCostItem]
    installation_pen: float
    costs, installation_pen = process_fixed_costs(
        data.get('fixed_costs', []), converter,
    )

    # 5. Carta Fianza
    cf_orig: float
    cf_pen: float
    cf_orig, cf_pen = calculate_carta_fianza(
        data.get('aplica_carta_fianza', False),
        data.get('tasa_carta_fianza', 0.0),
        plazo,
        mrc_orig,
        data.get('mrc_currency', Currency.PEN),
        converter,
    )

    # 6. Revenue & pre-commission margin
    total_revenue: float = nrc_pen + (mrc_pen * plazo)
    total_expense_pre: float = installation_pen + (monthly_expense_pen * plazo)
    gm_pre: float = total_revenue - total_expense_pre
    gm_ratio: float = (gm_pre / total_revenue) if total_revenue else 0.0

    # 7. Commission
    comisiones: float = _prepare_and_calculate_commission(
        data, total_revenue, gm_pre, gm_ratio, mrc_pen,
    )

    # 8. Timeline
    timeline: TimelineDict
    fixed_applied: float
    ncf_list: list[float]
    timeline, fixed_applied, ncf_list = build_timeline(
        plazo + 1, nrc_pen, mrc_pen, comisiones, cf_pen,
        monthly_expense_pen, data.get('fixed_costs', []),
    )

    # 9. KPIs
    total_expense: float = comisiones + fixed_applied + (monthly_expense_pen * plazo) + cf_pen
    kpis: KPIResult = calculate_kpis(
        ncf_list, total_revenue, total_expense, data.get('costo_capital_anual', 0),
    )

    return {
        'mrc_original': mrc_orig,
        'mrc_pen': mrc_pen,
        'nrc_original': nrc_orig,
        'nrc_pen': nrc_pen,
        **kpis,
        'comisiones': comisiones,
        'comisiones_rate': (comisiones / total_revenue) if total_revenue else 0.0,
        'costo_instalacion': fixed_applied,
        'costo_instalacion_ratio': (fixed_applied / total_revenue) if total_revenue else 0.0,
        'costo_carta_fianza': cf_pen,
        'aplica_carta_fianza': data.get('aplica_carta_fianza', False),
        'timeline': timeline,
    }
