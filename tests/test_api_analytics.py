from fastapi.testclient import TestClient

from app.api.analytics import load_metrics
from app.main import app
from app.state import MetricsRecord

client = TestClient(app)


def test_fixture_product_gets_rate_and_labeled_recommendation():
    response = client.get("/api/products/demo-mug-001/analytics")
    assert response.status_code == 200
    body = response.json()
    assert body["productId"] == "demo-mug-001"
    assert body["source"] == "synthetic"
    assert (body["sessions"], body["orders"]) == (240, 6)
    assert body["conversionRate"] == 0.025
    assert body["recommendationRule"] == "healthy_conversion"
    assert body["recommendation"].startswith("Synthetic sample data")


def test_unknown_product_is_not_found():
    response = client.get("/api/products/no-such-product/analytics")
    assert response.status_code == 404


def test_zero_sessions_returns_no_rate():
    record = MetricsRecord(
        productId="empty-001", source="synthetic", periodDays=7, sessions=0, orders=0
    )
    app.dependency_overrides[load_metrics] = lambda: [record]
    try:
        response = client.get("/api/products/empty-001/analytics")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["conversionRate"] is None
    assert response.json()["recommendationRule"] == "no_traffic"


def test_route_is_published_in_openapi_schema():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/products/{productId}/analytics" in paths
