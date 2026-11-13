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
from decimal import Decimal
from typing import Any, Literal

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
      variants(first: 1) {
        nodes {
          id
        }
      }
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

# Since 2024-04 ProductCreateInput has no variants: productCreate makes one default variant at
# price 0.00, and its price is set with a variant mutation afterwards.
SET_VARIANT_PRICE = """
mutation SetDefaultVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants {
      id
      price
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

    Product type, vendor and tags go into `productCreate`; a price needs a second request on
    the default variant. If that second request fails, the hidden DRAFT exists with price
    0.00 and this raises ShopifyError. A retry then finds the product by handle and returns
    it without sending a price, like any existing product, so the owner sets the price in the
    Shopify admin; nothing is created twice.
    """
    client = client or ShopifyClient()
    handle = product_handle(record.productId)
    found = client.graphql(FIND_PRODUCT, {"handle": handle}).get("productByIdentifier")
    if found is not None and PRODUCT_TAG not in found.get("tags", []):
        raise ShopifyError(f"Handle {handle!r} belongs to a product CommerceCrew did not create")
    product = found
    if product is None:
        fields: dict[str, Any] = {
            "title": record.title,
            "descriptionHtml": _description_html(record.description),
            "handle": handle,
            "status": "DRAFT",
            "tags": [PRODUCT_TAG, *(tag for tag in record.tags if tag != PRODUCT_TAG)],
        }
        if record.productType:
            fields["productType"] = record.productType
        if record.vendor:
            fields["vendor"] = record.vendor
        variables: dict[str, Any] = {"product": fields}
        if image_url:
            variables["media"] = [
                {"originalSource": image_url, "mediaContentType": "IMAGE", "alt": record.title}
            ]
        product = client.mutate(CREATE_PRODUCT, variables, "productCreate").get("product")
        if not product:
            raise ShopifyError("productCreate returned no product")
        if record.price is not None:
            _set_default_price(client, str(product["id"]), product, record.price)
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


PURCHASE_FACTS = """
query ProductPurchaseFacts($id: ID!, $publicationId: ID!) {
  shop {
    currencyCode
  }
  product(id: $id) {
    status
    publishedOnPublication(publicationId: $publicationId)
    variants(first: 1) {
      nodes {
        id
        price
        availableForSale
      }
    }
  }
}
"""

NotBuyableReason = Literal["not_found", "not_active", "not_published", "no_variant", "sold_out"]


@dataclass(frozen=True)
class PurchaseInfo:
    """Whether a product can be bought now; `checkout_url` is set only when it can."""

    status: str | None
    published: bool
    variant_id: str | None
    price: str | None
    currency: str | None
    available: bool
    checkout_url: str | None
    reason: NotBuyableReason | None


def purchase_info(
    record: ProductRecord,
    *,
    publication_id: str | None = None,
    client: ShopifyClient | None = None,
) -> PurchaseInfo:
    """Look up status, Online Store publication and first variant; link via a cart permalink."""
    if not record.shopifyProductId:
        raise ShopifyError(f"Product {record.productId!r} has no Shopify product to sell")
    client = client or ShopifyClient()
    publication_id = publication_id or online_store_publication_id(client)
    found = client.graphql(
        PURCHASE_FACTS, {"id": record.shopifyProductId, "publicationId": publication_id}
    )
    currency = (found.get("shop") or {}).get("currencyCode")
    product = found.get("product")
    if product is None:
        return PurchaseInfo(None, False, None, None, currency, False, None, "not_found")
    status = product.get("status")
    published = bool(product.get("publishedOnPublication"))
    variants = (product.get("variants") or {}).get("nodes") or []
    variant = variants[0] if variants else {}
    variant_id = variant.get("id")
    price = variant.get("price")
    available = bool(variant.get("availableForSale"))
    reason: NotBuyableReason | None = None
    if status != "ACTIVE":
        reason = "not_active"
    elif not published:
        reason = "not_published"
    elif not variant_id:
        reason = "no_variant"
    elif not available:
        reason = "sold_out"
    checkout_url = None
    if reason is None:
        # Cart permalink: https://{shop}/cart/{variant_id}:{quantity} opens checkout directly.
        numeric_id = str(variant_id).rsplit("/", 1)[-1]
        if not numeric_id.isdigit():
            raise ShopifyError(f"Unexpected variant id {variant_id!r}")
        checkout_url = f"https://{client.config.store_domain}/cart/{numeric_id}:1"
    return PurchaseInfo(
        status=str(status) if status else None,
        published=published,
        variant_id=str(variant_id) if variant_id else None,
        price=str(price) if price is not None else None,
        currency=str(currency) if currency else None,
        available=available,
        checkout_url=checkout_url,
        reason=reason,
    )


CREATE_TEST_ORDER = """
mutation CreateTestOrder($order: OrderCreateOrderInput!) {
  orderCreate(order: $order) {
    order {
      id
      name
      test
      displayFinancialStatus
      totalPriceSet { shopMoney { amount currencyCode } }
    }
    userErrors { field message }
  }
}
"""


class NotBuyableError(ShopifyError):
    """The product cannot be bought now, so no order was sent; `reason` says why."""

    def __init__(self, message: str, reason: NotBuyableReason) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ShopifyTestOrder:
    """A Shopify test order: recorded in the admin, no real payment taken."""

    order_id: str
    name: str
    total: str
    currency: str
    test: bool


def create_test_order(
    record: ProductRecord,
    *,
    publication_id: str | None = None,
    client: ShopifyClient | None = None,
) -> ShopifyTestOrder:
    """Record a PAID test order for one unit, only if `purchase_info` says it can be bought.

    The order is always sent with `test: true`, so this cannot create a real order. It is not
    idempotent: each call records another test order.
    """
    client = client or ShopifyClient()
    info = purchase_info(record, publication_id=publication_id, client=client)
    if info.reason is not None or not info.variant_id:
        reason: NotBuyableReason = info.reason or "no_variant"
        raise NotBuyableError(f"Product {record.productId!r} cannot be bought: {reason}", reason)
    order_input = {
        "test": True,
        "financialStatus": "PAID",
        "lineItems": [{"variantId": info.variant_id, "quantity": 1}],
    }
    result = client.mutate(CREATE_TEST_ORDER, {"order": order_input}, "orderCreate")
    order = result.get("order")
    if not order or not order.get("id"):
        raise ShopifyError("orderCreate returned no order")
    if order.get("test") is not True:
        raise ShopifyError(f"Shopify did not record order {order.get('name')!r} as a test order")
    money = (order.get("totalPriceSet") or {}).get("shopMoney") or {}
    return ShopifyTestOrder(
        order_id=str(order["id"]),
        name=str(order.get("name") or ""),
        total=str(money.get("amount") or ""),
        currency=str(money.get("currencyCode") or ""),
        test=True,
    )


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


def _set_default_price(
    client: ShopifyClient, product_id: str, product: Mapping[str, Any], price: Decimal
) -> None:
    variants = (product.get("variants") or {}).get("nodes") or []
    if not variants or not variants[0].get("id"):
        raise ShopifyError(f"Draft {product_id} was created without a variant to price")
    # Money is a decimal string; the format also turns an exponent such as 1E+3 into 1000.00.
    variant = {"id": variants[0]["id"], "price": f"{price:.2f}"}
    try:
        client.mutate(
            SET_VARIANT_PRICE,
            {"productId": product_id, "variants": [variant]},
            "productVariantsBulkUpdate",
        )
    except ShopifyError as error:
        raise ShopifyError(
            f"Draft {product_id} was created but its price was not set: {error}"
        ) from error


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
