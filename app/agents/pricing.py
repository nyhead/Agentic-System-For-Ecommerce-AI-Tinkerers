"""Deterministic comparable-price statistics, a starting-price proposal and a margin check.

The proposal places the starting price at an observed point of the comparable distribution that
matches the merchant's goal (lower quartile, median or upper quartile). It applies no invented
percentage adjustments for condition, provenance, accessories or selling speed.
"""

import statistics
from collections.abc import Sequence
from typing import Literal

from app.state import (
    Comparable,
    ConfidenceLabel,
    CostInputs,
    MatchLevel,
    PriceStats,
    PriceType,
    PricingGoal,
    PricingRecommendation,
    ProfitabilityCheck,
)

ProfitStatus = Literal["unchecked", "passes", "below_minimum_margin", "floor_above_market_range"]

MIN_FOR_RANGE = 3
MIN_FOR_RECOMMENDATION = 5
WIDE_DISPERSION = 0.5  # interquartile range divided by the median

_GOAL_POINT = {
    "faster_sale": ("lowerQuartile", "the lower quartile, to favor a faster sale"),
    "balanced": ("median", "the median, a balanced position"),
    "preserve_margin": ("upperQuartile", "the upper quartile, to preserve margin"),
}


def _round(value: float) -> float:
    return round(value, 2)


def price_stats(
    comparables: Sequence[Comparable], price_type: PriceType, match: MatchLevel
) -> list[PriceStats]:
    """One PriceStats per currency over item prices; shipping is not added in."""
    groups: dict[str, list[Comparable]] = {}
    for comp in comparables:
        if comp.priceType == price_type and comp.match == match and comp.price is not None:
            groups.setdefault(comp.price.currency, []).append(comp)
    result = []
    for currency, comps in sorted(groups.items()):
        values = sorted(c.price.amount for c in comps if c.price is not None)
        quartiles = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else []
        result.append(
            PriceStats(
                currency=currency,
                priceType=price_type,
                match=match,
                count=len(values),
                sampleRefs=[c.ref for c in comps],
                minimum=_round(values[0]),
                lowerQuartile=_round(quartiles[0]) if quartiles else None,
                median=_round(statistics.median(values)),
                upperQuartile=_round(quartiles[2]) if quartiles else None,
                maximum=_round(values[-1]),
            )
        )
    return result


def choose_basis(stats: Sequence[PriceStats], currency: str) -> PriceStats | None:
    """Prefer verified sales, then asking prices; same-model matches only; target currency."""
    for price_type in ("sold", "asking"):
        for item in stats:
            if (
                item.currency == currency
                and item.priceType == price_type
                and item.match == "same_model"
                and item.count >= MIN_FOR_RANGE
            ):
                return item
    return None


def recommend(
    stats: Sequence[PriceStats],
    *,
    currency: str,
    goal: PricingGoal,
    identity_confirmed: bool,
    missing_critical: Sequence[str],
    freshness: str,
) -> PricingRecommendation:
    basis = choose_basis(stats, currency)
    if basis is None:
        counts = ", ".join(f"{s.count} {s.priceType} ({s.match}, {s.currency})" for s in stats)
        return PricingRecommendation(
            status="insufficient_evidence",
            currency=currency,
            rationale=[
                f"Fewer than {MIN_FOR_RANGE} same-model comparables with a {currency} price "
                f"were found. Found: {counts or 'none'}.",
                "No price is proposed; add identifying details or widen the market and retry.",
            ],
            confidence="low",
            confidenceReasons=["Too few relevant comparables to describe a market range"],
        )
    point_field, point_words = _GOAL_POINT[goal]
    start = getattr(basis, point_field)
    if start is None:
        start = basis.median
    low = basis.lowerQuartile if basis.lowerQuartile is not None else basis.minimum
    high = basis.upperQuartile if basis.upperQuartile is not None else basis.maximum
    kind = "completed sales" if basis.priceType == "sold" else "current asking prices"
    rationale = [
        f"Based on {basis.count} same-model {kind} in {currency}, item price without shipping.",
        f"Observed: lowest {basis.minimum:g}, lower quartile {low:g}, median {basis.median:g}, "
        f"upper quartile {high:g}, highest {basis.maximum:g} {currency}.",
        f"The starting price sits at {point_words}. It describes where comparable listings "
        "sit; it is not a proven optimal price.",
        "No adjustment is applied for condition, service history or accessories: the evidence "
        "does not support a specific amount for those.",
    ]
    if basis.priceType == "asking":
        rationale.append("Asking prices show what sellers want, not what buyers paid.")

    reasons: list[str] = [f"{basis.count} same-model comparables", freshness]
    downgrades: list[str] = []
    dispersion = (high - low) / basis.median if basis.median else None
    if basis.count < MIN_FOR_RECOMMENDATION:
        downgrades.append(f"Only {basis.count} comparables (fewer than {MIN_FOR_RECOMMENDATION})")
    if basis.priceType != "sold":
        downgrades.append("No verified sale prices in the basis; asking prices only")
    if dispersion is not None and dispersion > WIDE_DISPERSION:
        downgrades.append(f"Wide price spread: the middle half spans {dispersion:.0%} of median")
    elif dispersion is not None:
        reasons.append(f"Middle half of prices spans {dispersion:.0%} of the median")
    if not identity_confirmed:
        downgrades.append("Product identity is read from photos and not yet confirmed")
    if missing_critical:
        downgrades.append("Missing facts that move price: " + ", ".join(missing_critical))
    confidence: ConfidenceLabel
    if basis.count < MIN_FOR_RECOMMENDATION or len(downgrades) >= 3:
        confidence = "low"
    elif downgrades:
        confidence = "medium"
    else:
        confidence = "high"
    provisional = basis.count < MIN_FOR_RECOMMENDATION or not identity_confirmed
    return PricingRecommendation(
        status="provisional" if provisional else "recommended",
        currency=currency,
        startingPrice=_round(start),
        rangeLow=_round(low),
        rangeHigh=_round(high),
        basis=f"{basis.priceType}:{basis.match}:{basis.currency}",
        rationale=rationale,
        confidence=confidence,
        confidenceReasons=reasons + downgrades,
    )


def check_profitability(
    costs: CostInputs | None, recommendation: PricingRecommendation | None
) -> ProfitabilityCheck:
    """Margin = profit / price; markup = profit / unit cost. Missing costs leave it unchecked."""
    if costs is None or costs.unitCost is None:
        return ProfitabilityCheck(
            status="unchecked", assumptions=["No unit cost supplied; profitability is unchecked"]
        )
    if recommendation is None or recommendation.startingPrice is None:
        return ProfitabilityCheck(
            status="unchecked", assumptions=["No supported starting price to check against costs"]
        )
    if recommendation.currency != costs.currency:
        return ProfitabilityCheck(
            status="unchecked",
            assumptions=[
                f"Costs are in {costs.currency} but the price is in {recommendation.currency}; "
                "no exchange rate was applied"
            ],
        )
    fee = costs.sellingFeeRate
    fixed = costs.sellingCostsFixed
    assumptions = [f"Unit cost {costs.unitCost:g} {costs.currency}"]
    assumptions.append(
        f"Selling fee {fee:.1%} of the sale price"
        if fee is not None
        else "No percentage selling fee supplied; none is included"
    )
    assumptions.append(
        f"Fixed selling costs {fixed:g} {costs.currency} per unit"
        if fixed is not None
        else "No fixed selling costs supplied; none are included"
    )
    fee_rate, fixed_cost = fee or 0.0, fixed or 0.0
    price = recommendation.startingPrice
    profit = price * (1 - fee_rate) - fixed_cost - costs.unitCost
    margin = profit / price if price else None
    markup = profit / costs.unitCost if costs.unitCost else None
    minimum: float | None = None
    status: ProfitStatus = "passes"
    if costs.minMarginRate is not None:
        denominator = 1 - fee_rate - costs.minMarginRate
        assumptions.append(f"Minimum margin {costs.minMarginRate:.1%} of the sale price")
        if denominator <= 0:
            assumptions.append("Fee plus minimum margin reach 100%; no price can satisfy both")
            status = "floor_above_market_range"
        else:
            minimum = _round((costs.unitCost + fixed_cost) / denominator)
            if recommendation.rangeHigh is not None and minimum > recommendation.rangeHigh:
                status = "floor_above_market_range"
            elif minimum > price:
                status = "below_minimum_margin"
    return ProfitabilityCheck(
        status=status,
        assumptions=assumptions,
        minimumPrice=minimum,
        marginAtStart=round(margin, 4) if margin is not None else None,
        markupAtStart=round(markup, 4) if markup is not None else None,
    )
