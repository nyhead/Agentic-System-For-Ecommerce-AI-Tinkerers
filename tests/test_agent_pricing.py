from app.agents.pricing import check_profitability, price_stats, recommend
from app.state import Comparable, CostInputs, PricingGoal, PricingRecommendation


def comp(
    ref: str,
    amount: float,
    *,
    currency: str = "EUR",
    price_type: str = "sold",
    match: str = "same_model",
) -> Comparable:
    return Comparable.model_validate(
        {
            "ref": ref,
            "source": "ebay-sold",
            "actorId": "a/b",
            "sourceUrl": f"https://x/{ref}",
            "retrievedAt": "2026-11-13",
            "title": "t",
            "price": {"amount": amount, "currency": currency},
            "priceType": price_type,
            "match": match,
        }
    )


SALES = [comp(f"C{i}", v) for i, v in enumerate([600, 700, 800, 900, 1000], 1)]


def run(
    comps: list[Comparable], goal: PricingGoal = "balanced", confirmed: bool = True
) -> PricingRecommendation:
    stats = price_stats(comps, "sold", "same_model") + price_stats(comps, "asking", "same_model")
    return recommend(
        stats,
        currency="EUR",
        goal=goal,
        identity_confirmed=confirmed,
        missing_critical=[],
        freshness="recent",
    )


def test_quartiles_are_computed_in_code() -> None:
    [stats] = price_stats(SALES, "sold", "same_model")
    assert (
        stats.minimum,
        stats.lowerQuartile,
        stats.median,
        stats.upperQuartile,
        stats.maximum,
    ) == (600, 700, 800, 900, 1000)
    assert stats.count == 5 and stats.sampleRefs == ["C1", "C2", "C3", "C4", "C5"]


def test_goal_picks_an_observed_point_without_invented_adjustments() -> None:
    assert run(SALES, "faster_sale").startingPrice == 700
    assert run(SALES, "balanced").startingPrice == 800
    assert run(SALES, "preserve_margin").startingPrice == 900
    result = run(SALES)
    assert (result.rangeLow, result.rangeHigh, result.status) == (700, 900, "recommended")


def test_asking_prices_and_sales_are_never_pooled() -> None:
    mixed = SALES[:2] + [comp(f"A{i}", 2000, price_type="asking") for i in range(3)]
    result = run(mixed)
    assert result.basis == "asking:same_model:EUR"
    assert any("not what buyers paid" in line for line in result.rationale)


def test_other_currencies_do_not_feed_the_recommendation() -> None:
    usd = [comp(f"U{i}", 900, currency="USD") for i in range(6)]
    result = run(usd)
    assert result.status == "insufficient_evidence" and result.startingPrice is None


def test_too_few_comparables_gives_insufficient_evidence() -> None:
    result = run(SALES[:2])
    assert result.status == "insufficient_evidence"
    assert result.startingPrice is None and result.rangeLow is None


def test_unconfirmed_identity_makes_the_price_provisional() -> None:
    result = run(SALES, confirmed=False)
    assert result.status == "provisional" and result.confidence == "medium"
    assert any("read from photos" in r for r in result.confidenceReasons)


REC = PricingRecommendation(
    status="recommended", currency="EUR", startingPrice=800, rangeLow=700, rangeHigh=900
)


def test_missing_costs_leave_profitability_unchecked() -> None:
    assert check_profitability(None, REC).status == "unchecked"
    assert check_profitability(CostInputs(currency="EUR"), REC).status == "unchecked"


def test_margin_and_markup_are_distinct() -> None:
    check = check_profitability(CostInputs(currency="EUR", unitCost=400), REC)
    assert check.status == "passes"
    assert check.marginAtStart == 0.5 and check.markupAtStart == 1.0
    assert any("No percentage selling fee" in a for a in check.assumptions)


def test_cost_floor_above_market_range_is_flagged() -> None:
    costs = CostInputs(currency="EUR", unitCost=700, sellingFeeRate=0.1, minMarginRate=0.25)
    check = check_profitability(costs, REC)
    assert check.status == "floor_above_market_range"
    assert check.minimumPrice is not None and check.minimumPrice > 900


def test_floor_inside_range_but_above_start_is_below_minimum_margin() -> None:
    costs = CostInputs(currency="EUR", unitCost=600, minMarginRate=0.28)
    check = check_profitability(costs, REC)
    assert check.status == "below_minimum_margin" and check.minimumPrice == 833.33


def test_cost_currency_mismatch_is_not_converted() -> None:
    check = check_profitability(CostInputs(currency="USD", unitCost=100), REC)
    assert check.status == "unchecked"
