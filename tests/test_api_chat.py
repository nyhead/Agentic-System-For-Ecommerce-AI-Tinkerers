from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.sales import SalesReply
from app.api.chat import SALES_UNAVAILABLE, get_answerer
from app.api.products import get_workflow
from app.main import app
from app.state import ProductRecord
from app.tools.llm import LLMError
from app.tools.shopify import ShopifyError

PRODUCT = {
    "productId": "mug-1",
    "shopifyProductId": "gid://shopify/Product/1",
    "title": "Blue mug",
    "description": "A blue mug.",
}
REPLY = SalesReply(
    answer="It holds 350 ml.",
    checkoutUrl="https://shop.example/cart/1:1",
    price="24.90",
    currency="EUR",
)


class StubWorkflow:
    def __init__(self, states: dict[str, dict[str, Any]]) -> None:
        self.states = states

    def snapshot(self, product_id: str) -> dict[str, Any] | None:
        return self.states.get(product_id)


@pytest.fixture
def asked() -> Iterator[list[tuple[ProductRecord, str]]]:
    calls: list[tuple[ProductRecord, str]] = []
    workflow = StubWorkflow({"mug-1": {"productId": "mug-1", "product": PRODUCT}, "empty": {}})
    app.dependency_overrides[get_workflow] = lambda: workflow
    yield calls
    app.dependency_overrides.clear()


def answering(result: SalesReply | Exception, calls: list[tuple[ProductRecord, str]]) -> None:
    def answer(product: ProductRecord, question: str) -> SalesReply:
        calls.append((product, question))
        if isinstance(result, Exception):
            raise result
        return result

    app.dependency_overrides[get_answerer] = lambda: answer


def test_chat_returns_the_sales_reply(asked: list[tuple[ProductRecord, str]]) -> None:
    answering(REPLY, asked)
    response = TestClient(app).post("/api/products/mug-1/chat", json={"question": "How big?"})
    assert response.status_code == 200
    assert response.json() == REPLY.model_dump()
    product, question = asked[0]
    assert product.shopifyProductId == "gid://shopify/Product/1"
    assert question == "How big?"


@pytest.mark.parametrize("product_id", ["unknown", "empty"])
def test_chat_without_a_drafted_product_is_404(
    product_id: str, asked: list[tuple[ProductRecord, str]]
) -> None:
    answering(REPLY, asked)
    response = TestClient(app).post(f"/api/products/{product_id}/chat", json={"question": "Hi"})
    assert response.status_code == 404
    assert asked == []


def test_chat_rejects_an_empty_question(asked: list[tuple[ProductRecord, str]]) -> None:
    answering(REPLY, asked)
    response = TestClient(app).post("/api/products/mug-1/chat", json={"question": ""})
    assert response.status_code == 422


def test_chat_value_error_is_400(asked: list[tuple[ProductRecord, str]]) -> None:
    answering(ValueError("question is empty"), asked)
    response = TestClient(app).post("/api/products/mug-1/chat", json={"question": "  "})
    assert response.status_code == 400
    assert response.json()["detail"] == "question is empty"


@pytest.mark.parametrize("error", [LLMError("no answer"), ShopifyError("timeout")])
def test_chat_upstream_failure_is_502(
    error: Exception, asked: list[tuple[ProductRecord, str]]
) -> None:
    answering(error, asked)
    response = TestClient(app).post("/api/products/mug-1/chat", json={"question": "Hi"})
    assert response.status_code == 502
    assert response.json()["detail"] == SALES_UNAVAILABLE
