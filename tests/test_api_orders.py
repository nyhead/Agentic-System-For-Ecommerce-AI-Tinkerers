from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.sales import SimulatedSale
from app.api.orders import TEST_ORDER_FAILED, get_recorder
from app.api.products import get_workflow
from app.main import app
from app.state import ProductRecord
from app.tools.shopify import NotBuyableError, ShopifyError

PRODUCT = {
    "productId": "mug-1",
    "shopifyProductId": "gid://shopify/Product/1",
    "title": "Blue mug",
    "description": "A blue mug.",
}
SALE = SimulatedSale(
    testOrderName="#1001",
    testOrderAdminUrl="https://admin.shopify.com/store/demo-shop/orders/5550001",
    total="24.0",
    currency="EUR",
)


class StubWorkflow:
    def __init__(self, states: dict[str, dict[str, Any]]) -> None:
        self.states = states

    def snapshot(self, product_id: str) -> dict[str, Any] | None:
        return self.states.get(product_id)


@pytest.fixture
def recorded() -> Iterator[list[ProductRecord]]:
    calls: list[ProductRecord] = []
    workflow = StubWorkflow({"mug-1": {"productId": "mug-1", "product": PRODUCT}, "empty": {}})
    app.dependency_overrides[get_workflow] = lambda: workflow
    yield calls
    app.dependency_overrides.clear()


def recording(result: SimulatedSale | Exception, calls: list[ProductRecord]) -> None:
    def record_sale(product: ProductRecord) -> SimulatedSale:
        calls.append(product)
        if isinstance(result, Exception):
            raise result
        return result

    app.dependency_overrides[get_recorder] = lambda: record_sale


def test_test_order_returns_the_simulated_sale(recorded: list[ProductRecord]) -> None:
    recording(SALE, recorded)
    response = TestClient(app).post("/api/products/mug-1/test-order")
    assert response.status_code == 200
    assert response.json() == {
        "simulatedPurchase": True,
        "testOrder": True,
        "testOrderName": "#1001",
        "testOrderAdminUrl": "https://admin.shopify.com/store/demo-shop/orders/5550001",
        "total": "24.0",
        "currency": "EUR",
    }
    assert recorded[0].shopifyProductId == "gid://shopify/Product/1"


@pytest.mark.parametrize("product_id", ["unknown", "empty"])
def test_test_order_without_a_drafted_product_is_404(
    product_id: str, recorded: list[ProductRecord]
) -> None:
    recording(SALE, recorded)
    response = TestClient(app).post(f"/api/products/{product_id}/test-order")
    assert response.status_code == 404
    assert recorded == []


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (NotBuyableError("draft", "not_active"), "not active yet"),
        (NotBuyableError("hidden", "not_published"), "not published"),
        (ValueError("product 'mug-1' has no Shopify draft"), "no Shopify draft"),
    ],
)
def test_test_order_for_unbuyable_product_is_409(
    error: Exception, words: str, recorded: list[ProductRecord]
) -> None:
    recording(error, recorded)
    response = TestClient(app).post("/api/products/mug-1/test-order")
    assert response.status_code == 409
    assert words in response.json()["detail"]


def test_test_order_shopify_failure_is_502(recorded: list[ProductRecord]) -> None:
    recording(ShopifyError("orderCreate failed: boom"), recorded)
    response = TestClient(app).post("/api/products/mug-1/test-order")
    assert response.status_code == 502
    assert response.json()["detail"] == TEST_ORDER_FAILED
