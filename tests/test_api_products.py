import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import graph
from app.api.products import get_workflow
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n fake image bytes"
PHOTO_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
EVENT = {"agent": "product", "action": "Wrote a draft title and description", "ok": True}

MARKETING = {
    "seoTitle": "Blue mug",
    "metaDescription": "A blue ceramic mug.",
    "socialAds": [
        {"channel": "instagram", "copy": "Morning coffee, bluer."},
        {"channel": "facebook", "copy": "A glazed blue mug."},
    ],
}


class StubWorkflow:
    """Records calls and answers like graph.Workflow without a model or Shopify."""

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self, product_id: str, notes: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", {"product_id": product_id, "notes": notes, **kwargs}))
        if not product_id.strip():
            raise ValueError("product_id is empty")
        if product_id in self.states:
            raise graph.WorkflowExistsError(f"A workflow for product {product_id!r} exists")
        state = {
            "productId": product_id,
            "notes": notes,
            "product": {"productId": product_id, "title": "Blue mug", "description": "Mug."},
            "marketing": MARKETING,
            "approval": "pending",
            "events": [{**EVENT, "at": "2026-11-13T14:00:00+00:00"}],
        }
        self.states[product_id] = state
        return state

    def decide(self, product_id: str, approved: bool) -> dict[str, Any]:
        self.calls.append(("decide", {"product_id": product_id, "approved": approved}))
        if product_id not in self.states:
            raise graph.UnknownWorkflowError(product_id)
        state = self.states[product_id]
        if state["approval"] != "pending":
            raise graph.NotAwaitingApprovalError(product_id)
        state["approval"] = "approved" if approved else "rejected"
        return state

    def snapshot(self, product_id: str) -> dict[str, Any] | None:
        return self.states.get(product_id)

    def products(self) -> list[str]:
        return list(self.states)

    def events(self) -> list[dict[str, Any]]:
        return [
            {**event, "productId": product_id}
            for product_id, state in self.states.items()
            for event in state["events"]
        ]


@pytest.fixture
def stub() -> Any:
    workflow = StubWorkflow()
    app.dependency_overrides[get_workflow] = lambda: workflow
    yield workflow
    app.dependency_overrides.pop(get_workflow, None)


@pytest.fixture
def client(stub: StubWorkflow) -> TestClient:
    return TestClient(app)


def test_draft_from_photo_and_notes_pauses_for_approval(client, stub):
    response = client.post("/api/products/draft", json={"notes": "Blue mug", "photo": PHOTO_URL})

    assert response.status_code == 201
    body = response.json()
    assert body["approval"] == "pending"
    assert body["error"] is None
    assert body["productId"].startswith("product-")
    assert body["marketing"]["socialAds"][0]["copy"] == "Morning coffee, bluer."
    assert "photo" not in body and "notes" not in body
    _, call = stub.calls[0]
    assert call["photo"] == PNG
    assert call["photo_mime_type"] == "image/png"
    assert call["image_url"] is None


def test_draft_passes_fixture_metrics_for_a_known_product(client, stub):
    client.post("/api/products/draft", json={"notes": "Mug", "productId": "demo-mug-001"})

    metrics = stub.calls[0][1]["metrics"]
    assert metrics.productId == "demo-mug-001"
    assert metrics.source == "synthetic"


def test_draft_without_metrics_for_a_new_product(client, stub):
    client.post("/api/products/draft", json={"notes": "Mug"})

    assert stub.calls[0][1]["metrics"] is None


def test_draft_needs_a_photo_or_notes(client, stub):
    assert client.post("/api/products/draft", json={"notes": "  "}).status_code == 422
    assert stub.calls == []


@pytest.mark.parametrize(
    "photo",
    [
        "not a data url",
        "data:text/plain;base64,aGk=",
        "data:image/png;base64,!!!",
        "data:image/png;base64,",
    ],
)
def test_draft_rejects_an_unusable_photo(client, stub, photo):
    assert client.post("/api/products/draft", json={"photo": photo}).status_code == 422
    assert stub.calls == []


def test_draft_rejects_photo_and_image_url_together(client, stub):
    body = {"photo": PHOTO_URL, "imageUrl": "https://example.com/mug.jpg"}
    assert client.post("/api/products/draft", json=body).status_code == 422


def test_draft_rejects_an_unsafe_product_id(client, stub):
    body = {"notes": "Mug", "productId": "../etc"}
    assert client.post("/api/products/draft", json=body).status_code == 422


def test_draft_for_an_existing_workflow_conflicts(client, stub):
    body = {"notes": "Mug", "productId": "mug-1"}
    assert client.post("/api/products/draft", json=body).status_code == 201
    assert client.post("/api/products/draft", json=body).status_code == 409


def test_approval_publishes_once(client, stub):
    product_id = client.post("/api/products/draft", json={"notes": "Mug"}).json()["productId"]

    approved = client.post(f"/api/products/{product_id}/approval", json={"approved": True})
    again = client.post(f"/api/products/{product_id}/approval", json={"approved": True})

    assert approved.status_code == 200
    assert approved.json()["approval"] == "approved"
    assert again.status_code == 409


def test_rejection_is_recorded(client, stub):
    product_id = client.post("/api/products/draft", json={"notes": "Mug"}).json()["productId"]

    response = client.post(f"/api/products/{product_id}/approval", json={"approved": False})

    assert response.json()["approval"] == "rejected"
    assert stub.calls[-1] == ("decide", {"product_id": product_id, "approved": False})


def test_approval_for_an_unknown_product_is_not_found(client, stub):
    assert client.post("/api/products/nope/approval", json={"approved": True}).status_code == 404


def test_approval_requires_a_boolean(client, stub):
    response = client.post("/api/products/x/approval", json={"approved": "maybe"})
    assert response.status_code == 422
    assert stub.calls == []


def test_products_list_and_detail(client, stub):
    product_id = client.post("/api/products/draft", json={"notes": "Mug"}).json()["productId"]

    listed = client.get("/api/products").json()
    detail = client.get(f"/api/products/{product_id}")

    assert [view["productId"] for view in listed] == [product_id]
    assert detail.status_code == 200
    assert detail.json()["product"]["title"] == "Blue mug"
    assert client.get("/api/products/nope").status_code == 404


def test_events_are_tagged_with_their_product(client, stub):
    product_id = client.post("/api/products/draft", json={"notes": "Mug"}).json()["productId"]

    events = client.get("/api/events").json()

    assert events == [{**EVENT, "at": "2026-11-13T14:00:00+00:00", "productId": product_id}]


def test_analytics_route_still_matches_before_product_detail(client):
    assert client.get("/api/products/demo-mug-001/analytics").json()["conversionRate"] == 0.025
