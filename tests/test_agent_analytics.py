import json
from pathlib import Path

import pytest

from app.agents.analytics import (
    LOW_CONVERSION_RATE,
    MIN_SESSIONS_TO_JUDGE,
    analyze,
    conversion_rate,
    metrics_for_product,
)
from app.state import MetricsRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_metrics():
    return MetricsRecord.model_validate_json((FIXTURES / "metrics.json").read_text())


def metrics(sessions, orders, source="observed", period_days=7, product_id="demo-mug-001"):
    return MetricsRecord(
        productId=product_id,
        source=source,
        periodDays=period_days,
        sessions=sessions,
        orders=orders,
    )


def test_fixture_gives_rate_and_healthy_recommendation():
    result = analyze(fixture_metrics())
    assert result.sessions == 240
    assert result.orders == 6
    assert result.periodDays == 7
    assert result.conversionRate == pytest.approx(0.025)
    assert result.recommendationRule == "healthy_conversion"
    assert result.recommendation == (
        "Synthetic sample data, not real shop visits: 240 visits and 6 orders over 7 days, "
        "a 2.5% conversion rate, at or above the 2% benchmark. If your real numbers look like "
        "this, post your launch ads to bring more visitors; the listing converts well."
    )


def test_zero_sessions_gives_no_rate():
    assert conversion_rate(0, 0) is None
    result = analyze(metrics(sessions=0, orders=0))
    assert result.conversionRate is None
    assert result.recommendationRule == "no_traffic"
    assert result.recommendation.startswith(
        "This product had no visits over 7 days, so there is no conversion rate yet."
    )


def test_conversion_rate_is_orders_divided_by_sessions():
    assert conversion_rate(240, 6) == pytest.approx(0.025)
    assert conversion_rate(3, 0) == 0


@pytest.mark.parametrize(
    ("sessions", "orders", "rule"),
    [
        (0, 0, "no_traffic"),
        (1, 0, "low_traffic"),
        (MIN_SESSIONS_TO_JUDGE - 1, 0, "low_traffic"),
        (MIN_SESSIONS_TO_JUDGE - 1, 50, "low_traffic"),
        (MIN_SESSIONS_TO_JUDGE, 0, "no_orders"),
        (MIN_SESSIONS_TO_JUDGE, 1, "low_conversion"),
        (500, 9, "low_conversion"),
        (500, 10, "healthy_conversion"),
        (MIN_SESSIONS_TO_JUDGE, 2, "healthy_conversion"),
    ],
)
def test_each_rule_and_its_boundaries(sessions, orders, rule):
    assert analyze(metrics(sessions, orders)).recommendationRule == rule


def test_benchmark_boundary_is_inclusive():
    assert 10 / 500 == LOW_CONVERSION_RATE
    assert analyze(metrics(500, 10)).recommendationRule == "healthy_conversion"


def test_recommendations_name_the_numbers():
    assert analyze(metrics(50, 1)).recommendation == (
        "This product had 50 visits and 1 order over 7 days, a 2.0% conversion rate, but that is "
        "too few visits to judge it. Bring in more visitors first, for example by posting your "
        "launch ads."
    )
    assert analyze(metrics(300, 0, period_days=1)).recommendation == (
        "This product had 300 visits but no orders over 1 day. Check that the price, photos and "
        "description answer what buyers want to know."
    )
    assert analyze(metrics(500, 9)).recommendation == (
        "This product had 500 visits and 9 orders over 7 days, a 1.8% conversion rate, below "
        "the 2% benchmark. Improve the product photos and description so more visitors buy."
    )


def test_rate_just_under_benchmark_never_reads_as_meeting_it():
    result = analyze(metrics(10_000, 199))
    assert result.recommendationRule == "low_conversion"
    assert "a 1.9% conversion rate, below the 2% benchmark" in result.recommendation


@pytest.mark.parametrize(("sessions", "orders"), [(0, 0), (50, 1), (300, 0), (500, 9), (240, 6)])
def test_synthetic_label_is_preserved_in_every_rule(sessions, orders):
    synthetic = analyze(metrics(sessions, orders, source="synthetic"))
    assert synthetic.source == "synthetic"
    assert synthetic.recommendation.startswith("Synthetic sample data, not real shop visits: ")
    assert "If your real numbers look like this, " in synthetic.recommendation
    observed = analyze(metrics(sessions, orders, source="observed"))
    assert observed.source == "observed"
    assert "synthetic" not in observed.recommendation.lower()


def test_rephrase_replaces_the_recommendation():
    result = analyze(fixture_metrics(), rephrase=lambda text: " Synthetic: keep posting ads. ")
    assert result.recommendation == "Synthetic: keep posting ads."
    assert result.recommendationRule == "healthy_conversion"


def test_rephrase_that_drops_the_synthetic_label_or_is_empty_is_ignored():
    default = analyze(fixture_metrics()).recommendation
    assert analyze(fixture_metrics(), rephrase=lambda text: "Keep posting ads.").recommendation == (
        default
    )
    assert analyze(fixture_metrics(), rephrase=lambda text: "  ").recommendation == default
    observed = analyze(metrics(240, 6), rephrase=lambda text: "Keep posting ads.")
    assert observed.recommendation == "Keep posting ads."


def test_metrics_for_product_selects_by_product_id():
    mug = fixture_metrics()
    other = metrics(10, 1, product_id="demo-tee-002")
    assert metrics_for_product([other, mug], "demo-mug-001") is mug
    assert metrics_for_product([other, mug], "missing-product") is None
    assert metrics_for_product([], "demo-mug-001") is None


def test_result_json_shape_matches_what_the_ui_sees():
    payload = json.loads(analyze(fixture_metrics()).model_dump_json())
    assert list(payload) == [
        "productId",
        "source",
        "periodDays",
        "sessions",
        "orders",
        "conversionRate",
        "recommendationRule",
        "recommendation",
    ]
    assert payload["productId"] == "demo-mug-001"
    assert payload["source"] == "synthetic"
    assert payload["conversionRate"] == pytest.approx(0.025)
    no_traffic = json.loads(analyze(metrics(0, 0)).model_dump_json())
    assert no_traffic["conversionRate"] is None
