import json
import logging
import threading

from app.tools.posthog import PostHogClient, synchronous

KEY = "phc_secret_test_key"


class FakeTransport:
    def __init__(self, result=200):
        self.result = result
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout}
        )
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def client(transport, **kwargs):
    return PostHogClient(transport=transport, dispatch=synchronous, **kwargs)


def test_nothing_is_sent_without_a_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_API_KEY", raising=False)
    transport = FakeTransport()
    client(transport).capture("listing_drafted", "demo-mug-001", {"ok": True})
    assert transport.calls == []


def test_request_url_and_body_from_the_environment(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", KEY)
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com/")
    transport = FakeTransport()

    client(transport).capture(
        "listing_drafted", "demo-mug-001", {"agent": "product"}, timestamp="2026-11-13T15:00:00Z"
    )

    [call] = transport.calls
    assert call["url"] == "https://eu.i.posthog.com/i/v0/e/"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"] == {
        "api_key": KEY,
        "event": "listing_drafted",
        "distinct_id": "demo-mug-001",
        "properties": {"agent": "product"},
        "timestamp": "2026-11-13T15:00:00Z",
    }
    assert 0 < call["timeout"] <= 5


def test_default_host_is_us_cloud(monkeypatch):
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    transport = FakeTransport()
    client(transport, api_key=KEY).capture("x", "p")
    assert transport.calls[0]["url"] == "https://us.i.posthog.com/i/v0/e/"


def test_failures_never_raise_and_never_log_the_key(monkeypatch, caplog):
    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", KEY)
    with caplog.at_level(logging.DEBUG, logger="app.tools.posthog"):
        client(FakeTransport(OSError(f"refused for {KEY}"))).capture("x", "p")
        client(FakeTransport(503)).capture("x", "p")
        PostHogClient(dispatch=lambda send: 1 / 0).capture("x", "p")
    assert "refused for ***" in caplog.text
    assert "HTTP 503" in caplog.text
    assert "ZeroDivisionError" in caplog.text
    assert KEY not in caplog.text
    assert KEY not in repr(PostHogClient(api_key=KEY))


def test_default_dispatch_sends_off_the_calling_thread(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_API_KEY", KEY)
    sent = threading.Event()
    threads = []

    def transport(url, headers, body, timeout):
        threads.append(threading.current_thread())
        sent.set()
        return 200

    PostHogClient(transport=transport).capture("x", "p")
    assert sent.wait(2)
    assert threads[0] is not threading.current_thread()
