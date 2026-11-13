"""Fire-and-forget PostHog event capture that can never slow down or break the caller."""

import json
import logging
import os
import threading
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

# (url, headers, body, timeout seconds) -> HTTP status
Transport = Callable[[str, Mapping[str, str], bytes, float], int]
# Runs one send; the default runs it on a daemon thread.
Dispatch = Callable[[Callable[[], None]], None]

DEFAULT_HOST = "https://us.i.posthog.com"
CAPTURE_PATH = "/i/v0/e/"


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status: int = response.status
        return status


def in_background(send: Callable[[], None]) -> None:
    threading.Thread(target=send, name="posthog-capture", daemon=True).start()


def synchronous(send: Callable[[], None]) -> None:
    send()


class PostHogClient:
    """Sends events to `POST {POSTHOG_HOST}/i/v0/e/`; does nothing without a project API key."""

    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        *,
        timeout: float = 2.0,
        transport: Transport = urllib_transport,
        dispatch: Dispatch = in_background,
    ) -> None:
        self._api_key = api_key
        self._host = host
        self._timeout = timeout
        self._transport = transport
        self._dispatch = dispatch

    def __repr__(self) -> str:
        return "PostHogClient()"

    def capture(
        self,
        event: str,
        distinct_id: str,
        properties: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Queue one event; never raises."""
        try:
            key = (self._api_key or os.environ.get("POSTHOG_PROJECT_API_KEY", "")).strip()
            if not key:
                return
            host = (self._host or os.environ.get("POSTHOG_HOST", "")).strip() or DEFAULT_HOST
            payload: dict[str, Any] = {
                "api_key": key,
                "event": event,
                "distinct_id": distinct_id,
                "properties": dict(properties or {}),
            }
            if timestamp is not None:
                payload["timestamp"] = timestamp
            url = f"{host.rstrip('/')}{CAPTURE_PATH}"
            body = json.dumps(payload).encode()
            self._dispatch(lambda: self._send(url, body, event, key))
        except Exception as error:
            logger.debug("PostHog capture of %r skipped: %s", event, _redact(error, self._api_key))

    def _send(self, url: str, body: bytes, event: str, key: str) -> None:
        headers = {"Content-Type": "application/json", "User-Agent": "commercecrew/0.1"}
        try:
            status = self._transport(url, headers, body, self._timeout)
            if not 200 <= status < 300:
                logger.debug("PostHog capture of %r returned HTTP %s", event, status)
        except Exception as error:
            logger.debug("PostHog capture of %r failed: %s", event, _redact(error, key))


def _redact(error: Exception, key: str | None) -> str:
    text = f"{type(error).__name__}: {error}"
    key = (key or os.environ.get("POSTHOG_PROJECT_API_KEY", "")).strip()
    return text.replace(key, "***") if key else text
