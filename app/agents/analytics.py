"""Analytics agent: conversion rate and one rule-based next action, computed without a model."""

from collections.abc import Callable, Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.state import MetricsRecord

# Below this many visits one order moves the rate by more than a percentage point: too noisy.
MIN_SESSIONS_TO_JUDGE = 100
# Commonly cited online-store conversion is about 2-3%; below 2% the listing is underperforming.
LOW_CONVERSION_RATE = 0.02

RecommendationRule = Literal[
    "no_traffic", "low_traffic", "no_orders", "low_conversion", "healthy_conversion"
]

_ACTIONS: dict[RecommendationRule, str] = {
    "no_traffic": "share the product link on your social channels to bring in first visitors.",
    "low_traffic": "bring in more visitors first, for example by posting your launch ads.",
    "no_orders": "check that the price, photos and description answer what buyers want to know.",
    "low_conversion": "improve the product photos and description so more visitors buy.",
    "healthy_conversion": "post your launch ads to bring more visitors; the listing converts well.",
}


class AnalyticsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productId: str
    source: Literal["synthetic", "observed"]
    periodDays: int
    sessions: int
    orders: int
    conversionRate: float | None
    recommendationRule: RecommendationRule
    recommendation: str


def conversion_rate(sessions: int, orders: int) -> float | None:
    """Orders divided by sessions; None when there were no sessions."""
    if sessions == 0:
        return None
    return orders / sessions


def metrics_for_product(records: Iterable[MetricsRecord], product_id: str) -> MetricsRecord | None:
    """Return the first metrics record for the product, or None when there is none."""
    return next((record for record in records if record.productId == product_id), None)


def analyze(
    metrics: MetricsRecord, rephrase: Callable[[str], str] | None = None
) -> AnalyticsResult:
    """Compute the conversion rate and choose one next action for the merchant."""
    rate = conversion_rate(metrics.sessions, metrics.orders)
    rule = _choose_rule(metrics.sessions, metrics.orders, rate)
    recommendation = _recommendation(metrics, rule)
    if rephrase is not None:
        rephrased = rephrase(recommendation).strip()
        # A rewrite that loses the synthetic label would present sample numbers as real ones.
        keeps_label = metrics.source == "observed" or "synthetic" in rephrased.lower()
        if rephrased and keeps_label:
            recommendation = rephrased
    return AnalyticsResult(
        productId=metrics.productId,
        source=metrics.source,
        periodDays=metrics.periodDays,
        sessions=metrics.sessions,
        orders=metrics.orders,
        conversionRate=rate,
        recommendationRule=rule,
        recommendation=recommendation,
    )


def _choose_rule(sessions: int, orders: int, rate: float | None) -> RecommendationRule:
    if rate is None:
        return "no_traffic"
    if sessions < MIN_SESSIONS_TO_JUDGE:
        return "low_traffic"
    if orders == 0:
        return "no_orders"
    if rate < LOW_CONVERSION_RATE:
        return "low_conversion"
    return "healthy_conversion"


def _percent(orders: int, sessions: int) -> str:
    # Integer tenths, rounded down, so a rate just under the benchmark never reads as meeting it.
    tenths = orders * 1000 // sessions
    return f"{tenths // 10}.{tenths % 10}%"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _facts(metrics: MetricsRecord, rule: RecommendationRule) -> str:
    period = f"over {_count(metrics.periodDays, 'day')}"
    visits = _count(metrics.sessions, "visit")
    if rule == "no_traffic":
        return f"no visits {period}, so there is no conversion rate yet"
    if rule == "no_orders":
        return f"{visits} but no orders {period}"
    rate = f"a {_percent(metrics.orders, metrics.sessions)} conversion rate"
    facts = f"{visits} and {_count(metrics.orders, 'order')} {period}, {rate}"
    if rule == "low_traffic":
        return f"{facts}, but that is too few visits to judge it"
    benchmark = f"the {LOW_CONVERSION_RATE:.0%} benchmark"
    if rule == "low_conversion":
        return f"{facts}, below {benchmark}"
    return f"{facts}, at or above {benchmark}"


def _recommendation(metrics: MetricsRecord, rule: RecommendationRule) -> str:
    facts = _facts(metrics, rule)
    action = _ACTIONS[rule]
    if metrics.source == "synthetic":
        return (
            f"Synthetic sample data, not real shop visits: {facts}. "
            f"If your real numbers look like this, {action}"
        )
    return f"This product had {facts}. {action[0].upper()}{action[1:]}"
