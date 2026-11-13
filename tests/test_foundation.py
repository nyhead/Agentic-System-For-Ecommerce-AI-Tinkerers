import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.state import AnalyticsResult, MarketingResult, MetricsRecord, ProductRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_health_starts_without_credentials():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_existing_team_fixtures_share_a_product():
    product = ProductRecord.model_validate_json((FIXTURES / "product.json").read_text())
    metrics = MetricsRecord.model_validate_json((FIXTURES / "metrics.json").read_text())
    assert product.productId == metrics.productId
    assert metrics.source == "synthetic"


def test_negative_sessions_cannot_enter_workflow():
    values = json.loads((FIXTURES / "metrics.json").read_text())
    values["sessions"] = -1
    with pytest.raises(ValidationError):
        MetricsRecord.model_validate(values)


def test_marketing_contract_requires_two_ads():
    with pytest.raises(ValidationError):
        MarketingResult(seoTitle="Mug", metaDescription="Blue mug", socialAds=[])


def test_marketing_preserves_shared_json_copy_field():
    payload = {
        "seoTitle": "Mug",
        "metaDescription": "Blue mug",
        "socialAds": [
            {"channel": "social", "copy": "A blue ceramic mug."},
            {"channel": "social", "copy": "A glazed finish."},
        ],
    }
    assert MarketingResult.model_validate(payload).model_dump() == payload


ANALYTICS_PAYLOAD = {
    "productId": "demo-mug-001",
    "source": "synthetic",
    "periodDays": 7,
    "sessions": 240,
    "orders": 6,
    "conversionRate": 0.025,
    "recommendationRule": "healthy_conversion",
    "recommendation": "Synthetic sample data, not real shop visits: 240 visits and 6 orders.",
}


def test_analytics_contract_round_trips_json_field_names():
    assert AnalyticsResult.model_validate(ANALYTICS_PAYLOAD).model_dump() == ANALYTICS_PAYLOAD


def test_analytics_contract_allows_no_rate_without_sessions():
    payload = {**ANALYTICS_PAYLOAD, "sessions": 0, "orders": 0, "conversionRate": None}
    payload["recommendationRule"] = "no_traffic"
    assert AnalyticsResult.model_validate(payload).conversionRate is None


def test_analytics_contract_rejects_unknown_rule():
    with pytest.raises(ValidationError):
        AnalyticsResult.model_validate({**ANALYTICS_PAYLOAD, "recommendationRule": "guess"})
