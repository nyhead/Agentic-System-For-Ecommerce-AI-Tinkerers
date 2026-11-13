"""Shopify Admin GraphQL tool: idempotent draft products, publishing and image uploads."""

import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.state import ProductRecord

DEFAULT_API_VERSION = "2026-10"
PRODUCT_TAG = "commercecrew"
HANDLE_PREFIX = "commercecrew-"
# Catalog titles vary by store ("Channel Catalog 123 for Online Store"); the app handle does not.
ONLINE_STORE_APP_HANDLE = "online_store"
# CDNs often reject urllib's default "Python-urllib" agent with 403.
USER_AGENT = "commercecrew/0.1"

# (method, url, headers, body, timeout seconds) -> (HTTP status, response body)
Transport = Callable[[str, str, Mapping[str, str], bytes, float], tuple[int, bytes]]

FIND_PRODUCT = """
query FindProductByHandle($handle: String!) {
  productByIdentifier(identifier: {handle: $handle}) {
    id
    tags
    media(first: 1) {
      nodes {
        preview {
          image {
            url
          }
        }
      }
    }
  }
}
"""

CREATE_PRODUCT = """
mutation CreateDraftProduct($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
  productCreate(product: $product, media: $media) {
    product {
      id
      media(first: 1) {
        nodes {
          preview {
            image {
              url
            }
          }
        }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

APP_PUBLICATIONS = """
query AppPublications {
  publications(first: 50, catalogType: APP) {
    nodes {
      id
      catalog {
        ... on AppCatalog {
          apps(first: 5) {
            nodes {
              handle
            }
          }
        }
      }
    }
  }
}
"""

ACTIVATE_PRODUCT = """
mutation ActivateProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product {
      id
      status
    }
    userErrors {
      field
      message
    }
  }
}
"""

PUBLISH_PRODUCT = """
mutation PublishProduct($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors {
      field
      message
    }
  }
}
"""

STAGE_UPLOAD = """
mutation StageImageUpload($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters {
        name
        value
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""


class ShopifyError(Exception):
    """Any Shopify failure: missing configuration, HTTP, GraphQL `errors` or `userErrors`."""


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status: int = response.status
            data: bytes = response.read()
            return status, data
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@dataclass(frozen=True)
class ShopifyConfig:
    store_domain: str
    access_token: str = field(repr=False)
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ShopifyConfig":
        env = os.environ if env is None else env
        required = ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_ADMIN_ACCESS_TOKEN")
        missing = [name for name in required if not env.get(name, "").strip()]
        if missing:
            raise ShopifyError(f"Shopify is not configured; set {', '.join(missing)}")
        domain = env["SHOPIFY_STORE_DOMAIN"].strip().removeprefix("https://").rstrip("/")
        version = env.get("SHOPIFY_API_VERSION", "").strip() or DEFAULT_API_VERSION
        return cls(domain, env["SHOPIFY_ADMIN_ACCESS_TOKEN"].strip(), version)

    @property
    def graphql_url(self) -> str:
        return f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json"


class ShopifyClient:
    """Admin GraphQL client; reads the environment only when built without a config."""

    def __init__(
        self,
        config: ShopifyConfig | None = None,
        *,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 20.0,
        retry_delay: float = 2.0,
    ) -> None:
        self.config = config or ShopifyConfig.from_env()
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout
        self._retry_delay = retry_delay

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run one operation and return its `data`, retrying once if Shopify throttles."""
        body = json.dumps({"query": query, "variables": dict(variables or {})}).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": self.config.access_token,
        }
        for attempt in range(2):
            status, raw = self._send("POST", self.config.graphql_url, headers, body)
            payload = _json_object(raw) if status == 200 else None
            throttled = status == 429 or (payload is not None and _is_throttled(payload))
            if not throttled:
                break
            if attempt == 0:
                self._sleep(self._retry_delay)
        if throttled:
            raise self._error("Shopify throttled the request twice; try again shortly")
        if payload is None:
            raise self._error(f"Shopify Admin API returned HTTP {status}: {raw[:300]!r}")
        if payload.get("errors"):
            raise self._error(f"Shopify GraphQL errors: {_messages(payload['errors'])}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise self._error("Shopify response has no data")
        return data

    def mutate(self, query: str, variables: Mapping[str, Any], name: str) -> dict[str, Any]:
        """Run a mutation and return its payload, raising on `userErrors`."""
        result = self.graphql(query, variables).get(name)
        if not isinstance(result, dict):
            raise self._error(f"{name} returned no payload")
        if result.get("userErrors"):
            raise self._error(f"{name} failed: {_messages(result['userErrors'])}")
        return result

    def upload(self, url: str, body: bytes, content_type: str) -> None:
        """POST a file to a staged upload target. The Admin token is not sent there."""
        status, raw = self._send("POST", url, {"Content-Type": content_type}, body)
        if not 200 <= status < 300:
            raise self._error(f"Staged upload returned HTTP {status}: {raw[:300]!r}")

    def _send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, bytes]:
        headers = {**headers, "User-Agent": USER_AGENT}
        try:
            return self._transport(method, url, headers, body, self._timeout)
        # URLError, socket timeouts and connection failures; truncated or malformed responses
        except (OSError, http.client.HTTPException) as error:
            raise self._error(f"Shopify request failed: {error}") from None

    def _error(self, message: str) -> ShopifyError:
        token = self.config.access_token
        return ShopifyError(message.replace(token, "[redacted]") if token else message)


def product_handle(product_id: str) -> str:
    """Stable Shopify handle for our productId, used to find the product again on retries."""
    slug = re.sub(r"[^a-z0-9]+", "-", product_id.lower()).strip("-")
    if not slug:
        raise ShopifyError(f"productId {product_id!r} has no characters usable in a handle")
    return HANDLE_PREFIX + slug


def create_draft_product(
    record: ProductRecord,
    *,
    image_url: str | None = None,
    client: ShopifyClient | None = None,
) -> ProductRecord:
    """Create a DRAFT product once per productId; a repeated call returns the existing one.

    `image_url` is a public URL or a `resourceUrl` from `upload_image`. The returned
    `imageUrl` is Shopify's CDN URL once the image is processed, else the source URL.
    """
    client = client or ShopifyClient()
    handle = product_handle(record.productId)
    found = client.graphql(FIND_PRODUCT, {"handle": handle}).get("productByIdentifier")
    if found is not None and PRODUCT_TAG not in found.get("tags", []):
        raise ShopifyError(f"Handle {handle!r} belongs to a product CommerceCrew did not create")
    product = found
    if product is None:
        variables: dict[str, Any] = {
            "product": {
                "title": record.title,
                "descriptionHtml": _description_html(record.description),
                "handle": handle,
                "status": "DRAFT",
                "tags": [PRODUCT_TAG],
            }
        }
        if image_url:
            variables["media"] = [
                {"originalSource": image_url, "mediaContentType": "IMAGE", "alt": record.title}
            ]
        product = client.mutate(CREATE_PRODUCT, variables, "productCreate").get("product")
        if not product:
            raise ShopifyError("productCreate returned no product")
    media = product.get("media", {}).get("nodes", [])
    cdn_url = _preview_url(media)
    source_url = image_url if (media or found is None) else None
    return record.model_copy(
        update={
            "shopifyProductId": str(product["id"]),
            "imageUrl": cdn_url or source_url or record.imageUrl,
        }
    )


def online_store_publication_id(client: ShopifyClient | None = None) -> str:
    client = client or ShopifyClient()
    publications = (client.graphql(APP_PUBLICATIONS).get("publications") or {}).get("nodes") or []
    for publication in publications:
        apps = ((publication.get("catalog") or {}).get("apps") or {}).get("nodes") or []
        if any(app.get("handle") == ONLINE_STORE_APP_HANDLE for app in apps):
            return str(publication["id"])
    raise ShopifyError("No Online Store publication found; is the Online Store channel installed?")


def publish_product(
    record: ProductRecord,
    *,
    publication_id: str | None = None,
    client: ShopifyClient | None = None,
) -> ProductRecord:
    """Publish an approved product to the Online Store, then make it ACTIVE. Safe to repeat.

    Publishing comes first because a DRAFT stays hidden from customers even on a channel, so a
    failure between the two calls leaves the product a hidden DRAFT rather than ACTIVE.
    """
    if not record.shopifyProductId:
        raise ShopifyError(f"Product {record.productId!r} has no Shopify draft to publish")
    client = client or ShopifyClient()
    publication_id = publication_id or online_store_publication_id(client)
    product_id = record.shopifyProductId
    client.mutate(
        PUBLISH_PRODUCT,
        {"id": product_id, "input": [{"publicationId": publication_id}]},
        "publishablePublish",
    )
    client.mutate(
        ACTIVATE_PRODUCT, {"product": {"id": product_id, "status": "ACTIVE"}}, "productUpdate"
    )
    return record


def upload_image(
    data: bytes, filename: str, mime_type: str, *, client: ShopifyClient | None = None
) -> str:
    """Stage image bytes in Shopify and return the `resourceUrl` for `create_draft_product`."""
    client = client or ShopifyClient()
    stage_input = {"resource": "IMAGE", "filename": filename, "mimeType": mime_type}
    staged = client.mutate(
        STAGE_UPLOAD, {"input": [{**stage_input, "httpMethod": "POST"}]}, "stagedUploadsCreate"
    )
    targets = staged.get("stagedTargets") or []
    if not targets or not targets[0].get("url") or not targets[0].get("resourceUrl"):
        raise ShopifyError("stagedUploadsCreate returned no upload target")
    target = targets[0]
    fields = [(str(p["name"]), str(p["value"])) for p in target.get("parameters", [])]
    body, content_type = _multipart(fields, filename, mime_type, data)
    client.upload(str(target["url"]), body, content_type)
    return str(target["resourceUrl"])


def _multipart(
    fields: list[tuple[str, str]], filename: str, mime_type: str, data: bytes
) -> tuple[bytes, str]:
    # The staged target's parameters go first as form fields; the file must be the last part.
    boundary = uuid.uuid4().hex
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields
    ]
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f'name="file"; filename="{safe_name}"\r\nContent-Type: {mime_type}\r\n\r\n'.encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _description_html(description: str) -> str:
    paragraphs = [p.strip() for p in description.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def _preview_url(media: list[dict[str, Any]]) -> str | None:
    for node in media:
        url = ((node.get("preview") or {}).get("image") or {}).get("url")
        if url:
            return str(url)
    return None


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_throttled(payload: Mapping[str, Any]) -> bool:
    errors = payload.get("errors")
    return isinstance(errors, list) and any(
        isinstance(e, dict) and (e.get("extensions") or {}).get("code") == "THROTTLED"
        for e in errors
    )


def _messages(errors: Any) -> str:
    if not isinstance(errors, list):
        return str(errors)
    return "; ".join(
        f"{'.'.join(map(str, e.get('field') or []))}: {e.get('message')}".lstrip(": ")
        if isinstance(e, dict)
        else str(e)
        for e in errors
    )
