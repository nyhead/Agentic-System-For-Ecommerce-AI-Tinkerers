"""Apify REST client: start an Actor run under platform spending limits, poll it, read results.

The token comes from APIFY_TOKEN and is sent only in the Authorization header, never in a URL,
so it cannot leak through logged request lines. Starting a run is never retried: a lost response
to that POST may still have started a paid run, so the caller decides what to do next.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

APIFY_API = "https://api.apify.com/v2"
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"})
_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))

# (method, url, headers, body, timeout seconds) -> (HTTP status, response body)
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


class ApifyError(RuntimeError):
    """Any failure to talk to the Apify API."""


class ApifyNotConfiguredError(ApifyError):
    """APIFY_TOKEN is not set; no request was sent."""


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status: int = response.status
            return status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (OSError, http.client.HTTPException) as error:
        raise ApifyError(f"could not reach the Apify API: {error}") from None


@dataclass(frozen=True)
class RunLimits:
    """Platform-enforced bounds for one run; Apify stops charging at maxTotalChargeUsd."""

    max_items: int
    max_total_charge_usd: float
    timeout_secs: int
    memory_mbytes: int


@dataclass(frozen=True)
class ApifyRun:
    id: str
    status: str
    dataset_id: str | None
    usage_total_usd: float | None
    status_message: str | None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES


class ApifyClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = APIFY_API,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return "ApifyClient()"

    @property
    def configured(self) -> bool:
        return bool(self._resolved_token())

    def start_run(self, actor_id: str, run_input: Mapping[str, Any], limits: RunLimits) -> ApifyRun:
        """Start one run. Not retried, so one call can never start two paid runs."""
        query = urllib.parse.urlencode(
            {
                "timeout": limits.timeout_secs,
                "memory": limits.memory_mbytes,
                "maxItems": limits.max_items,
                "maxTotalChargeUsd": f"{limits.max_total_charge_usd:.2f}",
            }
        )
        path = f"/acts/{urllib.parse.quote(actor_id.replace('/', '~'), safe='~')}/runs?{query}"
        data = self._request("POST", path, json.dumps(dict(run_input)).encode(), retry=False)
        return _run(data)

    def get_run(self, run_id: str, *, wait_secs: int = 0) -> ApifyRun:
        """Read a run; with wait_secs (max 60) Apify holds the reply until it finishes."""
        wait = max(0, min(wait_secs, 60))
        path = f"/actor-runs/{urllib.parse.quote(run_id)}?waitForFinish={wait}"
        return _run(self._request("GET", path, None, retry=True, extra_timeout=wait))

    def dataset_items(self, dataset_id: str, limit: int) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"clean": "true", "format": "json", "limit": limit})
        path = f"/datasets/{urllib.parse.quote(dataset_id)}/items?{query}"
        items = self._request("GET", path, None, retry=True, envelope=False)
        if not isinstance(items, list):
            raise ApifyError("dataset items were not a JSON array")
        return [item for item in items if isinstance(item, dict)]

    def abort_run(self, run_id: str) -> None:
        self._request("POST", f"/actor-runs/{urllib.parse.quote(run_id)}/abort", b"", retry=False)

    def account_limits(self) -> dict[str, float | None]:
        """Best effort: this cycle's usage and limit in USD. Values the API omits stay None."""
        data = self._request("GET", "/users/me/limits", None, retry=True)
        limits = data.get("limits") if isinstance(data, dict) else None
        current = data.get("current") if isinstance(data, dict) else None
        return {
            "maxMonthlyUsageUsd": _number((limits or {}).get("maxMonthlyUsageUsd")),
            "monthlyUsageUsd": _number((current or {}).get("monthlyUsageUsd")),
        }

    def _resolved_token(self) -> str:
        return (self._token or os.environ.get("APIFY_TOKEN", "")).strip()

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        retry: bool,
        envelope: bool = True,
        extra_timeout: int = 0,
    ) -> Any:
        token = self._resolved_token()
        if not token:
            raise ApifyNotConfiguredError("Apify is not configured; set APIFY_TOKEN on the server")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "commercecrew/0.1",
        }
        url = f"{self._base_url}{path}"
        timeout = self._timeout + extra_timeout
        try:
            status, raw = self._transport(method, url, headers, body, timeout)
            if retry and status in _RETRY_STATUSES:
                self._sleep(self._retry_delay)
                status, raw = self._transport(method, url, headers, body, timeout)
        except ApifyError as error:
            raise ApifyError(self._redact(str(error), token)) from None
        if not 200 <= status < 300:
            snippet = raw[:300].decode("utf-8", "replace")
            raise ApifyError(self._redact(f"Apify API returned HTTP {status}: {snippet}", token))
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            raise ApifyError("Apify API returned a body that is not JSON") from None
        if not envelope:
            return parsed
        if not isinstance(parsed, dict) or "data" not in parsed:
            raise ApifyError("Apify API reply had no data field")
        return parsed["data"]

    @staticmethod
    def _redact(message: str, token: str) -> str:
        return message.replace(token, "***") if token else message


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _run(data: Any) -> ApifyRun:
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise ApifyError("Apify run reply had no run id")
    dataset = data.get("defaultDatasetId")
    message = data.get("statusMessage")
    return ApifyRun(
        id=data["id"],
        status=str(data.get("status", "UNKNOWN")),
        dataset_id=dataset if isinstance(dataset, str) else None,
        usage_total_usd=_number(data.get("usageTotalUsd")),
        status_message=message if isinstance(message, str) else None,
    )
