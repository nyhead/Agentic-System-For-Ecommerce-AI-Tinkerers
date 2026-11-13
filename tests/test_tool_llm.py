import base64
import io
import json
import urllib.error

import pytest
from pydantic import BaseModel

from app.state import MarketingResult
from app.tools.llm import Image, LLMClient, LLMError

KEY = "sk-test-secret-123"


class Listing(BaseModel):
    title: str
    price: float


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        return self.responses.pop(0)


def reply(content, status=200):
    return status, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def make_client(*responses, sleeps=None):
    transport = FakeTransport(*responses)
    client = LLMClient(
        "https://llm.example/v1/",
        KEY,
        "some/model",
        transport=transport,
        sleep=(sleeps if sleeps is not None else []).append,
    )
    return client, transport


def test_complete_sends_openai_compatible_request():
    client, transport = make_client(reply("A blue mug."))
    text = client.complete("Describe it.", system="Be brief.", temperature=0.2, max_tokens=50)
    assert text == "A blue mug."
    (call,) = transport.calls
    assert call["url"] == "https://llm.example/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"] == {
        "model": "some/model",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Describe it."},
        ],
        "temperature": 0.2,
        "max_tokens": 50,
    }


def test_optional_parameters_are_omitted():
    client, transport = make_client(reply("ok"))
    client.complete("Hi")
    assert transport.calls[0]["body"] == {
        "model": "some/model",
        "messages": [{"role": "user", "content": "Hi"}],
    }


def test_images_are_sent_as_content_parts():
    client, transport = make_client(reply("A mug."))
    photo = b"\x89PNG fake bytes"
    client.complete(
        "What is this?",
        images=[Image.from_bytes(photo, "image/png"), Image.from_url("https://cdn.example/m.jpg")],
    )
    content = transport.calls[0]["body"]["messages"][0]["content"]
    encoded = base64.b64encode(photo).decode()
    assert content == [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        {"type": "image_url", "image_url": {"url": "https://cdn.example/m.jpg"}},
    ]


def test_image_inputs_are_validated():
    with pytest.raises(ValueError):
        Image.from_url("http://insecure.example/m.jpg")
    with pytest.raises(ValueError):
        Image.from_bytes(b"data", "text/plain")
    with pytest.raises(ValueError):
        Image.from_bytes(b"", "image/png")


def test_reasoning_preamble_is_stripped():
    client, _ = make_client(reply("<think>hmm {not json}</think>\nFinal answer."))
    assert client.complete("Hi") == "Final answer."


@pytest.mark.parametrize(
    "content",
    [
        '{"title": "Mug", "price": 12.5}',
        'Sure! Here it is:\n```json\n{"title": "Mug", "price": 12.5}\n```\nEnjoy.',
        'Note {this} first. {"title": "Mug", "price": 12.5} Hope that helps!',
        '{"example": true}\n```\n{"title": "Mug", "price": 12.5}\n```',
    ],
)
def test_structured_extracts_wrapped_json(content):
    client, transport = make_client(reply(content))
    assert client.structured("List it.", Listing) == Listing(title="Mug", price=12.5)
    assert len(transport.calls) == 1
    prompt = transport.calls[0]["body"]["messages"][0]["content"]
    assert prompt.startswith("List it.")
    assert '"price"' in prompt


def test_structured_respects_shared_contract_aliases():
    ads = [{"channel": "instagram", "copy": "Sip in blue."}, {"channel": "x", "copy": "New mug."}]
    payload = {"seoTitle": "Blue Mug", "metaDescription": "A blue mug.", "socialAds": ads}
    client, _ = make_client(reply(json.dumps(payload)))
    result = client.structured("Write copy.", MarketingResult)
    assert result.model_dump() == payload


def test_structured_retries_with_validation_feedback():
    client, transport = make_client(
        reply('{"title": "Mug", "price": "cheap"}'),
        reply('{"title": "Mug", "price": 9}'),
    )
    assert client.structured("List it.", Listing, images=[Image.from_url("https://x.example/a")])
    retry = transport.calls[1]["body"]["messages"]
    assert retry[0] == transport.calls[0]["body"]["messages"][0]
    assert retry[1] == {"role": "assistant", "content": '{"title": "Mug", "price": "cheap"}'}
    assert retry[2]["role"] == "user"
    assert "price" in retry[2]["content"]
    assert "valid number" in retry[2]["content"]


def test_structured_gives_up_after_second_invalid_reply():
    client, transport = make_client(reply("no json here"), reply('{"title": "Mug"}'))
    with pytest.raises(LLMError, match="Listing after a retry: price: Field required"):
        client.structured("List it.", Listing)
    assert len(transport.calls) == 2
    assert "no JSON object" in transport.calls[1]["body"]["messages"][-1]["content"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_once_on_rate_limit_and_server_error(status):
    sleeps = []
    client, transport = make_client(reply("busy", status=status), reply("ok"), sleeps=sleeps)
    assert client.complete("Hi") == "ok"
    assert len(transport.calls) == 2
    assert sleeps == [1.0]


def test_gives_up_after_second_server_error():
    client, transport = make_client((502, b"bad gateway"), (502, b"bad gateway"))
    with pytest.raises(LLMError, match="HTTP 502: bad gateway"):
        client.complete("Hi")
    assert len(transport.calls) == 2


def test_client_errors_are_not_retried():
    client, transport = make_client((400, b'{"error": "model does not support images"}'))
    with pytest.raises(LLMError, match="HTTP 400"):
        client.complete("Hi")
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "raw", [b"<html>oops</html>", b'{"choices": []}', b'{"choices": [{"message": {}}]}']
)
def test_unexpected_response_bodies_raise_llm_error(raw):
    client, _ = make_client((200, raw))
    with pytest.raises(LLMError):
        client.complete("Hi")


def test_empty_reply_raises_llm_error():
    client, _ = make_client(reply("   "))
    with pytest.raises(LLMError, match="empty reply"):
        client.complete("Hi")


def test_missing_configuration_fails_only_on_call(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "")
    transport = FakeTransport()
    client = LLMClient(transport=transport)
    with pytest.raises(LLMError, match="set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"):
        client.complete("Hi")
    assert transport.calls == []


def test_configuration_is_read_from_environment_at_call_time(monkeypatch):
    transport = FakeTransport(reply("ok"))
    client = LLMClient(transport=transport)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.featherless.ai/v1")
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_MODEL", "env/model")
    assert client.complete("Hi") == "ok"
    call = transport.calls[0]
    assert call["url"] == "https://api.featherless.ai/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["body"]["model"] == "env/model"


def test_api_key_never_appears_in_errors():
    client, _ = make_client((401, f'{{"error": "invalid key {KEY}"}}'.encode()))
    with pytest.raises(LLMError) as raised:
        client.complete("Hi")
    assert KEY not in str(raised.value)
    assert "***" in str(raised.value)
    assert KEY not in repr(client)


def test_network_failure_becomes_llm_error_without_key():
    def unreachable(url, headers, body, timeout):
        raise LLMError(f"could not reach the model API: refused {headers['Authorization']}")

    client = LLMClient("https://llm.example/v1", KEY, "m", transport=unreachable)
    with pytest.raises(LLMError) as raised:
        client.complete("Hi")
    assert KEY not in str(raised.value)


def test_default_transport_maps_connection_errors(monkeypatch):
    def refuse(request, timeout):
        assert timeout == 5.0
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    client = LLMClient("https://llm.example/v1", KEY, "m", timeout=5.0)
    with pytest.raises(LLMError, match="could not reach the model API"):
        client.complete("Hi")


def test_default_transport_returns_http_errors_for_retry(monkeypatch):
    seen = []

    class Response(io.BytesIO):
        status = 200

    def urlopen(request, timeout):
        seen.append((request.full_url, request.get_method(), request.get_header("Authorization")))
        if len(seen) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
        return Response(reply("ok")[1])

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = LLMClient("https://llm.example/v1", KEY, "m", sleep=lambda seconds: None)
    assert client.complete("Hi") == "ok"
    assert seen == [("https://llm.example/v1/chat/completions", "POST", f"Bearer {KEY}")] * 2
