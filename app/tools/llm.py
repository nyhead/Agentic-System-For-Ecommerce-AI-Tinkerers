"""OpenAI-compatible Chat Completions adapter shared by every agent."""

import base64
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# (url, headers, body, timeout seconds) -> (HTTP status, response body)
Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]

_RETRY_STATUSES = frozenset({429}) | frozenset(range(500, 600))
# Sent when the caller sets no limit. Some hosts (Featherless) otherwise default max_tokens to
# the whole context window and reject the request, since that leaves no room for the prompt.
DEFAULT_MAX_TOKENS = 4096
# Reasoning models (e.g. DeepSeek-R1, Qwen3) may prefix the answer with their chain of thought.
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


class LLMError(RuntimeError):
    """Any failure to get a usable reply from the model API."""


@dataclass(frozen=True)
class Image:
    """An image content part; build it with `from_bytes` or `from_url`."""

    url: str

    def __post_init__(self) -> None:
        if not self.url.startswith(("https://", "data:image/")):
            raise ValueError("image URL must be https:// or a data:image/ URL")

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str) -> "Image":
        if not data:
            raise ValueError("image data is empty")
        if not mime_type.startswith("image/"):
            raise ValueError(f"not an image MIME type: {mime_type!r}")
        encoded = base64.b64encode(data).decode("ascii")
        return cls(f"data:{mime_type};base64,{encoded}")

    @classmethod
    def from_url(cls, url: str) -> "Image":
        return cls(url)


def urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status: int = response.status
            return status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (OSError, http.client.HTTPException) as error:
        raise LLMError(f"could not reach the model API: {error}") from None


class LLMClient:
    """Calls `POST {LLM_BASE_URL}/chat/completions`; settings not passed in are read per call."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        retry_delay: float = 1.0,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"LLMClient(model={self._model!r})"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[Image] = (),
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return the model's text reply to one user prompt, optionally with images."""
        messages = _messages(prompt, system, images)
        return self._chat(messages, temperature, max_tokens)

    def structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        images: Sequence[Image] = (),
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> T:
        """Return the reply parsed into `schema`, retrying once with the validation error.

        `on_retry`, when given, is called with that error before the retry; its errors are ignored.
        """
        schema_json = json.dumps(schema.model_json_schema())
        instruction = (
            f"{prompt}\n\nReply with a single JSON object that conforms to this JSON Schema. "
            f"Output only the JSON object: no prose and no Markdown code fences.\n{schema_json}"
        )
        messages = _messages(instruction, system, images)
        reply = self._chat(messages, temperature, max_tokens)
        try:
            return _parse(reply, schema)
        except ValueError as error:
            feedback = (
                f"That reply was invalid: {error}\n"
                "Reply again with only the corrected JSON object."
            )
            if on_retry is not None:
                try:
                    on_retry(str(error))
                except Exception:
                    pass
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": feedback},
        ]
        reply = self._chat(messages, temperature, max_tokens)
        try:
            return _parse(reply, schema)
        except ValueError as error:
            problem = f"model reply did not match {schema.__name__} after a retry: {error}"
            raise LLMError(self._redact(problem)) from None

    def _chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        base_url, api_key, model = self._config()
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        payload["max_tokens"] = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Providers behind a CDN often reject urllib's default "Python-urllib" agent with 403.
            "User-Agent": "commercecrew/0.1",
        }
        body = json.dumps(payload).encode()
        try:
            status, raw = self._transport(url, headers, body, self._timeout)
            if status in _RETRY_STATUSES:
                self._sleep(self._retry_delay)
                status, raw = self._transport(url, headers, body, self._timeout)
        except LLMError as error:
            raise LLMError(self._redact(str(error))) from None
        if not 200 <= status < 300:
            snippet = raw[:300].decode("utf-8", "replace")
            raise LLMError(self._redact(f"model API returned HTTP {status}: {snippet}"))
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise LLMError("model API returned an unexpected response body") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("model API returned an empty reply")
        return _THINK_BLOCK.sub("", content, count=1)

    def _config(self) -> tuple[str, str, str]:
        values = {
            "LLM_BASE_URL": self._base_url or os.environ.get("LLM_BASE_URL", ""),
            "LLM_API_KEY": self._api_key or os.environ.get("LLM_API_KEY", ""),
            "LLM_MODEL": self._model or os.environ.get("LLM_MODEL", ""),
        }
        missing = [name for name, value in values.items() if not value.strip()]
        if missing:
            raise LLMError(f"model API is not configured; set {', '.join(missing)}")
        base_url = values["LLM_BASE_URL"].strip()
        if not base_url.startswith(("https://", "http://")):
            raise LLMError("LLM_BASE_URL must start with https:// or http://")
        return base_url, values["LLM_API_KEY"].strip(), values["LLM_MODEL"].strip()

    def _redact(self, message: str) -> str:
        key = (self._api_key or os.environ.get("LLM_API_KEY", "")).strip()
        return message.replace(key, "***") if key else message


def _messages(prompt: str, system: str | None, images: Sequence[Image]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        parts += [{"type": "image_url", "image_url": {"url": image.url}} for image in images]
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def _parse(reply: str, schema: type[T]) -> T:
    """Validate the first JSON object in the reply that fits `schema`, ignoring fences and prose."""
    candidates = _json_objects(reply)
    if not candidates:
        raise ValueError("the reply contained no JSON object")
    first_error = ""
    for candidate in candidates:
        try:
            return schema.model_validate(candidate)
        except ValidationError as error:
            if not first_error:
                first_error = "; ".join(
                    f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
                    for issue in error.errors(include_url=False)
                )
    raise ValueError(first_error)


def _json_objects(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    found: list[object] = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        found.append(value)
        index = text.find("{", end)
    return found
