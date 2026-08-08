"""Point-in-time feature extraction from already-validated break facts."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from packages.remediation.models import BreakFacts

FEATURE_VERSION = "1.0.0"

BREAK_FAMILIES = (
    "MISSING_REQUIRED_SOURCE",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
    "DUPLICATE_SOURCE_CONFLICT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH",
    "ECONOMIC_VALUE_MISMATCH",
    "TRADE_OR_VALUE_DATE_MISMATCH",
    "LIFECYCLE_STATUS_MISMATCH",
    "POST_ACTION_VERIFICATION_FAILURE",
)

FEATURE_NAMES = (
    "log10_trade_value",
    "relative_value_gap_bps",
    "product_is_forward",
    *(f"family__{family.lower()}" for family in BREAK_FAMILIES),
    "field_is_base_amount",
    "field_is_quoted_rate",
    "expected_source_is_fix_execution",
    "observed_source_is_legacy_booking",
    "condition_is_decimal_tolerance",
    "currency_is_g10",
)

G10_CURRENCIES = frozenset({"USD", "EUR", "JPY", "GBP", "AUD", "NZD", "CAD", "CHF"})


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def feature_vector(facts: BreakFacts) -> tuple[float, ...]:
    """Return the immutable feature tuple in ``FEATURE_NAMES`` order.

    Only fields available at case-generation time are used.  No resolution
    outcome, evaluator truth, approval, or post-action value can leak in.
    """

    trade_value = _decimal(facts.trade_value_amount)
    expected = _decimal(facts.expected_value)
    observed = _decimal(facts.observed_value)

    absolute_trade_value = abs(float(trade_value)) if trade_value is not None else 0.0
    log10_trade_value = math.log10(max(absolute_trade_value, 1.0))

    relative_gap_bps = 0.0
    if expected is not None and observed is not None:
        denominator = max(abs(expected), Decimal("1"))
        relative_gap_bps = min(float(abs(expected - observed) / denominator * 10_000), 10_000.0)

    family_flags = tuple(1.0 if facts.break_family == family else 0.0 for family in BREAK_FAMILIES)
    return (
        log10_trade_value,
        relative_gap_bps,
        1.0 if facts.product_type == "FX_FORWARD" else 0.0,
        *family_flags,
        1.0 if facts.field_path == "/payload/base_amount" else 0.0,
        1.0 if facts.field_path == "/payload/quoted_rate" else 0.0,
        1.0 if facts.expected_source_system == "FIX_EXECUTION" else 0.0,
        1.0 if facts.observed_source_system == "MOCK_LEGACY_BOOKING" else 0.0,
        1.0 if facts.condition_code == "DECIMAL_OUTSIDE_TOLERANCE" else 0.0,
        1.0 if facts.trade_value_currency in G10_CURRENCIES else 0.0,
    )


__all__ = ["BREAK_FAMILIES", "FEATURE_NAMES", "FEATURE_VERSION", "feature_vector"]
