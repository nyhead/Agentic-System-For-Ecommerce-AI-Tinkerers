from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import research as research_api
from app.main import app
from tests.test_agent_research import fixture_service


class NoWorkflow:
    def snapshot(self, product_id: str) -> Any:
        return None


@pytest.fixture()
def client() -> Iterator[TestClient]:
    service = fixture_service()
    app.dependency_overrides[research_api.get_research] = lambda: service
    app.dependency_overrides[research_api.get_workflow] = lambda: NoWorkflow()
    yield TestClient(app)
    app.dependency_overrides.clear()


BODY = {
    "title": "Omega Seamaster De Ville — brown strap",
    "category": "Watches",
    "imageSuggestions": [
        {"name": "Brand", "value": "Omega"},
        {"name": "Model line", "value": "Seamaster De Ville"},
    ],
    "country": "DE",
    "currency": "EUR",
}


def test_start_poll_and_decide(client: TestClient) -> None:
    started = client.post("/api/products/p-omega/research", json=BODY)
    assert started.status_code == 202
    report = started.json()
    assert client.get(f"/api/research/{report['researchId']}").json()["status"] == (
        "ready_for_review"
    )
    assert (
        client.get("/api/products/p-omega/research").json()["researchId"] == (report["researchId"])
    )
    decided = client.post(
        f"/api/research/{report['researchId']}/decisions",
        json={"kind": "price", "changeIds": ["price"], "approved": True},
    )
    assert decided.status_code == 200
    assert decided.json()["decisions"][0]["kind"] == "price"


def test_stale_title_is_a_conflict(client: TestClient) -> None:
    report = client.post("/api/products/p-omega/research", json=BODY).json()
    response = client.post(
        f"/api/research/{report['researchId']}/decisions",
        json={
            "kind": "content",
            "changeIds": ["title"],
            "approved": True,
            "current": {"title": "Changed"},
        },
    )
    assert response.status_code == 409


def test_unknown_research_is_404(client: TestClient) -> None:
    assert client.get("/api/research/nope").status_code == 404
    assert client.get("/api/products/none/research").status_code == 404
