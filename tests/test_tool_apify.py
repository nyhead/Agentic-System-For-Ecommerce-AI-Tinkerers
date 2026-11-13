import json
from collections.abc import Mapping

import pytest

from app.tools.apify import ApifyClient, ApifyError, ApifyNotConfiguredError, RunLimits

LIMITS = RunLimits(max_items=10, max_total_charge_usd=0.2, timeout_secs=120, memory_mbytes=1024)


class Recorder:
    def __init__(self, *replies: tuple[int, object]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), body))
        status, payload = self.replies.pop(0)
        return status, json.dumps(payload).encode()


RUN = {"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}}


def test_start_run_sends_spending_limits_and_keeps_token_out_of_url() -> None:
    transport = Recorder((201, RUN))
    client = ApifyClient("secret-token", transport=transport, sleep=lambda _: None)
    run = client.start_run("caffein.dev/ebay-sold-listings", {"keywords": ["x"]}, LIMITS)
    method, url, headers, body = transport.calls[0]
    assert run.id == "run1" and run.dataset_id == "ds1"
    assert method == "POST"
    assert "/acts/caffein.dev~ebay-sold-listings/runs?" in url
    assert "maxItems=10" in url and "maxTotalChargeUsd=0.20" in url and "timeout=120" in url
    assert "secret-token" not in url
    assert headers["Authorization"] == "Bearer secret-token"
    assert json.loads(body or b"{}") == {"keywords": ["x"]}


def test_start_run_is_never_retried_so_one_call_cannot_start_two_paid_runs() -> None:
    transport = Recorder((500, {"error": "boom"}), (201, RUN))
    client = ApifyClient("t", transport=transport, sleep=lambda _: None)
    with pytest.raises(ApifyError):
        client.start_run("a/b", {}, LIMITS)
    assert len(transport.calls) == 1


def test_reads_are_retried_once_on_server_error() -> None:
    finished = {"data": {"id": "run1", "status": "SUCCEEDED", "usageTotalUsd": 0.031}}
    transport = Recorder((503, {}), (200, finished))
    run = ApifyClient("t", transport=transport, sleep=lambda _: None).get_run("run1")
    assert run.finished and run.usage_total_usd == 0.031
    assert len(transport.calls) == 2


def test_missing_token_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    transport = Recorder()
    client = ApifyClient(transport=transport)
    assert not client.configured
    with pytest.raises(ApifyNotConfiguredError):
        client.get_run("run1")
    assert transport.calls == []


def test_token_is_redacted_from_errors() -> None:
    transport = Recorder((401, {"error": "bad token tok-123"}))
    with pytest.raises(ApifyError) as caught:
        ApifyClient("tok-123", transport=transport).get_run("r")
    assert "tok-123" not in str(caught.value)
