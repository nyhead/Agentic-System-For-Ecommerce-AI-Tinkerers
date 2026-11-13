from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agents.support import (
    CustomerIdentity,
    DemoChatChannel,
    SupportDesk,
    demo_watch_catalog,
)
from app.api import support
from app.api.support import NOT_FOUND, get_desk
from app.main import app

PATH = "/api/support/conversations"
INTERNAL = ("evidence", "notes", "escalation", "results", "customerId", "verifiedCustomerId",
            "delivery", "intent", "missing_information", "merchantQuestion", "role")


@pytest.fixture
def desk() -> Iterator[SupportDesk]:
    desk = SupportDesk(catalog=demo_watch_catalog(), channel=DemoChatChannel())
    app.dependency_overrides[get_desk] = lambda: desk
    yield desk
    app.dependency_overrides.clear()


@pytest.fixture
def client(desk: SupportDesk) -> TestClient:
    return TestClient(app)


def open_watch_chat(client: TestClient) -> str:
    response = client.post(PATH, json={"customerName": "Hannah", "productId": "p-omega"})
    assert response.status_code == 201
    conversation_id: str = response.json()["conversationId"]
    return conversation_id


def send(client: TestClient, cid: str, text: str, mid: str = "c-1") -> dict[str, object]:
    response = client.post(f"{PATH}/{cid}/messages", json={"text": text, "clientMessageId": mid})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_open_a_chat_from_a_product_page(client: TestClient) -> None:
    response = client.post(PATH, json={"customerName": "Hannah", "productId": "p-omega"})
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"conversationId", "waitingForMerchant", "notice", "product", "messages"}
    assert body["product"]["title"].startswith("Omega Seamaster")
    assert body["messages"] == [] and body["waitingForMerchant"] is False


def test_open_a_general_chat_without_a_product(client: TestClient) -> None:
    response = client.post(PATH, json={"customerName": "Guest", "productId": None})
    assert response.status_code == 201
    assert response.json()["product"] is None
    assert client.post(PATH, json={}).status_code == 201


def test_open_rejects_an_invalid_product_id(client: TestClient) -> None:
    response = client.post(PATH, json={"customerName": "Hannah", "productId": "../etc"})
    assert response.status_code == 400
    assert response.json()["detail"] == "product_id is not a valid id"


def test_identity_fields_in_the_body_are_ignored(client: TestClient, desk: SupportDesk) -> None:
    response = client.post(PATH, json={"customerName": "Hannah", "customerId": "cust-hannah",
                                       "verified": True})
    assert response.status_code == 201
    assert desk.conversation(response.json()["conversationId"]).customerId is None


def test_post_a_question_about_the_demo_watch_and_load_the_chat(client: TestClient) -> None:
    cid = open_watch_chat(client)
    reply = send(client, cid, "What movement does it have?")
    assert set(reply) == {"conversationId", "messageId", "status", "reply"}
    assert reply["status"] == "replied" and reply["conversationId"] == cid
    assert isinstance(reply["reply"], dict) and "Automatic" in reply["reply"]["text"]
    assert reply["reply"]["author"] == "assistant"

    loaded = client.get(f"{PATH}/{cid}")
    assert loaded.status_code == 200
    messages = loaded.json()["messages"]
    assert [m["author"] for m in messages] == ["customer", "assistant"]
    assert messages[0]["clientMessageId"] == "c-1"
    assert messages[1]["clientMessageId"] is None


def test_a_retry_returns_the_same_reply_without_a_duplicate(client: TestClient) -> None:
    cid = open_watch_chat(client)
    first = send(client, cid, "What movement does it have?", "c-retry")
    again = send(client, cid, "What movement does it have?", "c-retry")
    assert again == first
    assert len(client.get(f"{PATH}/{cid}").json()["messages"]) == 2


def test_an_unanswerable_question_waits_for_the_shop_and_leaks_nothing(
    client: TestClient,
) -> None:
    cid = open_watch_chat(client)
    reply = send(client, cid, "Can I swim with this watch?")
    assert reply["status"] == "waiting_for_shop"
    loaded = client.get(f"{PATH}/{cid}")
    assert loaded.json()["waitingForMerchant"] is True
    for text in (str(reply), loaded.text):
        for field in INTERNAL:
            assert f"'{field}'" not in text and f'"{field}"' not in text
        assert "WATERPROOF" not in text and "photo-caseback" not in text


def test_unknown_and_other_customers_chats_look_the_same(
    client: TestClient, desk: SupportDesk
) -> None:
    owned = desk.open_conversation(
        "Marco",
        identity=CustomerIdentity(customerId="cust-marco", verified=True, method="store_session"),
    ).conversationId
    for cid in ("conv-nope", owned):
        loaded = client.get(f"{PATH}/{cid}")
        message = {"text": "Hi", "clientMessageId": "c-1"}
        posted = client.post(f"{PATH}/{cid}/messages", json=message)
        for response in (loaded, posted):
            assert response.status_code == 404
            assert response.json() == {"detail": NOT_FOUND}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"text": "   ", "clientMessageId": "c-1"}, 400),
        ({"text": "x" * 2001, "clientMessageId": "c-1"}, 400),
        ({"text": "Hi", "clientMessageId": "not valid!"}, 400),
        ({"text": "Hi"}, 422),
        ({"clientMessageId": "c-1"}, 422),
    ],
)
def test_invalid_messages_are_a_short_4xx(
    client: TestClient, payload: dict[str, str], code: int
) -> None:
    cid = open_watch_chat(client)
    response = client.post(f"{PATH}/{cid}/messages", json=payload)
    assert response.status_code == code
    assert response.json()["detail"]
    assert client.get(f"{PATH}/{cid}").json()["messages"] == []


def test_the_default_desk_is_built_lazily_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in support.LLM_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(support, "_desk", None)
    desk = get_desk()
    assert desk is get_desk()
    assert support._model() is None


def test_the_service_still_serves_the_chat_page_and_the_cockpit() -> None:
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/chat/").status_code == 200
    assert "chat.js" in client.get("/chat/").text
    assert client.get("/").status_code == 200
