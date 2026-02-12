"""
Financial Math Utilities.

Pure Python implementations of NPV and IRR calculations.
No external dependencies (no numpy/scipy needed).
"""

from __future__ import annotations

import math

__all__: list[str] = ["calculate_npv", "calculate_irr"]

# Threshold below which a floating-point value is treated as zero.
_ZERO_THRESHOLD: float = 1e-12

# Bounds for the Newton-Raphson IRR solver.  Rates outside this range
# are economically meaningless and indicate divergence.
_IRR_LOWER_BOUND: float = -0.999
_IRR_UPPER_BOUND: float = 100.0  # 10,000% — generous upper limit


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is NaN or +/-Inf."""
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")


def calculate_npv(rate: float, cash_flows: list[float]) -> float:
    """Calculate Net Present Value.

    Discounts each cash flow back to period 0 using the given periodic rate.

    Args:
        rate: Discount rate per period (e.g., monthly rate = annual_rate / 12).
              Must be greater than -1.0 to avoid division-by-zero behaviour.
        cash_flows: List of cash flows where index 0 is period 0 (t=0).
                    Must not be empty.  All values must be finite (no NaN/Inf).

    Returns:
        The NPV as a float.

    Raises:
        ValueError: If *cash_flows* is empty, *rate* <= -1.0, or any input
                    is NaN/Inf.
    """
    if not cash_flows:
        raise ValueError("cash_flows must not be empty.")

    _validate_finite(rate, "rate")

    for i, cf in enumerate(cash_flows):
        _validate_finite(cf, f"cash_flows[{i}]")

    if rate <= -1.0:
        raise ValueError(
            f"rate must be greater than -1.0, got {rate!r}. "
            "A rate of -1.0 or below causes division by zero in discounting."
        )

    npv: float = 0.0
    rate_is_zero: bool = abs(rate) < _ZERO_THRESHOLD

    for t, cf in enumerate(cash_flows):
        if rate_is_zero:
            npv += cf
        else:
            npv += cf / ((1.0 + rate) ** t)

    return npv


def calculate_irr(
    cash_flows: list[float],
    max_iterations: int = 1000,
    tolerance: float = 1e-7,
) -> float | None:
    """Calculate Internal Rate of Return using the Newton-Raphson method.

    The IRR is the discount rate at which the Net Present Value of *cash_flows*
    equals zero.

    Args:
        cash_flows: List of cash flows. Must contain at least two entries and
                    at least one sign change (otherwise no meaningful IRR
                    exists).  All values must be finite (no NaN/Inf).
        max_iterations: Maximum Newton-Raphson iterations before giving up.
        tolerance: Convergence tolerance for successive rate estimates.

    Returns:
        The IRR as a decimal (e.g., 0.05 for 5%), or ``None`` if the method
        does not converge within *max_iterations*. Returning ``None`` clearly
        distinguishes "could not compute" from a genuine 0% IRR.

    Raises:
        ValueError: If *cash_flows* has fewer than 2 entries or contains
                    NaN/Inf values.
    """
    if len(cash_flows) < 2:
        raise ValueError(
            f"cash_flows must contain at least 2 entries, got {len(cash_flows)}."
        )

    for i, cf in enumerate(cash_flows):
        _validate_finite(cf, f"cash_flows[{i}]")

    # Pre-check: IRR requires at least one sign change in the cash flows.
    # Without both positive and negative values, no rate can drive NPV to
    # zero — return None immediately instead of wasting iterations.
    has_positive: bool = any(cf > 0 for cf in cash_flows)
    has_negative: bool = any(cf < 0 for cf in cash_flows)
    if not has_positive or not has_negative:
        return None

    guess: float = 0.1

    for _ in range(max_iterations):
        npv: float = 0.0
        d_npv: float = 0.0

        for t, cf in enumerate(cash_flows):
            denominator: float = (1.0 + guess) ** t
            if abs(denominator) < _ZERO_THRESHOLD:
                # Denominator collapsed to zero -- cannot continue from here.
                return None
            npv += cf / denominator
            if t > 0:
                d_npv -= t * cf / ((1.0 + guess) ** (t + 1))

        # If the derivative is essentially flat, Newton-Raphson cannot step.
        if abs(d_npv) < _ZERO_THRESHOLD:
            return None

        new_guess: float = guess - npv / d_npv

        # Bounds clamping: if the solver diverges outside the economically
        # meaningful range, clamp it back.  This prevents runaway oscillation
        # and NaN/Inf propagation from extreme guesses.
        new_guess = max(_IRR_LOWER_BOUND, min(_IRR_UPPER_BOUND, new_guess))

        if abs(new_guess - guess) < tolerance:
            return new_guess

        guess = new_guess

    # Did not converge within the allowed iterations.
    return None
