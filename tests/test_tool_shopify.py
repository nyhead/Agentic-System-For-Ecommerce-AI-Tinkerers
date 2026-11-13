import json
import urllib.error

import pytest

from app.state import ProductRecord
from app.tools import shopify
from app.tools.shopify import ShopifyClient, ShopifyConfig, ShopifyError

TOKEN = "shpat_test_secret_token_123"
GRAPHQL_URL = "https://demo-shop.myshopify.com/admin/api/2026-10/graphql.json"
PRODUCT_GID = "gid://shopify/Product/1001"
CDN_URL = "https://cdn.shopify.com/s/files/mug.jpg"


class FakeShopify:
    """Transport stub: replays canned (status, body) responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def request(self, index):
        return json.loads(self.calls[index]["body"])


def make_client(fake, sleeps=None):
    config = ShopifyConfig("demo-shop.myshopify.com", TOKEN)
    sleep = sleeps.append if sleeps is not None else None
    return ShopifyClient(config, transport=fake, sleep=sleep or (lambda _: None), timeout=7.0)


def record():
    return ProductRecord(
        productId="demo-mug-001",
        title="Blue Ceramic Mug",
        description="A blue ceramic mug & handle.",
    )


def data(**fields):
    return 200, {"data": fields}


NOT_FOUND = data(productByIdentifier=None)
CREATED = data(
    productCreate={
        "product": {"id": PRODUCT_GID, "media": {"nodes": [{"preview": None}]}},
        "userErrors": [],
    }
)


def test_create_draft_sends_draft_product_with_image():
    fake = FakeShopify(NOT_FOUND, CREATED)
    result = shopify.create_draft_product(
        record(), image_url="https://example.com/mug.jpg", client=make_client(fake)
    )

    assert [call["url"] for call in fake.calls] == [GRAPHQL_URL, GRAPHQL_URL]
    create = fake.calls[1]
    assert create["method"] == "POST"
    assert create["timeout"] == 7.0
    assert create["headers"]["X-Shopify-Access-Token"] == TOKEN
    assert create["headers"]["Content-Type"] == "application/json"
    assert create["headers"]["User-Agent"] == "commercecrew/0.1"
    assert fake.request(0)["variables"] == {"handle": "commercecrew-demo-mug-001"}
    sent = fake.request(1)
    assert "productCreate(product: $product, media: $media)" in sent["query"]
    assert sent["variables"] == {
        "product": {
            "title": "Blue Ceramic Mug",
            "descriptionHtml": "<p>A blue ceramic mug &amp; handle.</p>",
            "handle": "commercecrew-demo-mug-001",
            "status": "DRAFT",
            "tags": ["commercecrew"],
        },
        "media": [
            {
                "originalSource": "https://example.com/mug.jpg",
                "mediaContentType": "IMAGE",
                "alt": "Blue Ceramic Mug",
            }
        ],
    }
    assert result.shopifyProductId == PRODUCT_GID
    assert result.imageUrl == "https://example.com/mug.jpg"
    assert result.productId == "demo-mug-001"


def test_create_draft_without_image_sends_no_media():
    created = data(productCreate={"product": {"id": PRODUCT_GID, "media": {"nodes": []}}})
    fake = FakeShopify(NOT_FOUND, created)
    result = shopify.create_draft_product(record(), client=make_client(fake))
    assert "media" not in fake.request(1)["variables"]
    assert result.shopifyProductId == PRODUCT_GID
    assert result.imageUrl is None


def test_second_create_returns_existing_product_without_mutation():
    existing = data(
        productByIdentifier={
            "id": PRODUCT_GID,
            "tags": ["commercecrew"],
            "media": {"nodes": [{"preview": {"image": {"url": CDN_URL}}}]},
        }
    )
    fake = FakeShopify(existing)
    result = shopify.create_draft_product(
        record(), image_url="https://example.com/mug.jpg", client=make_client(fake)
    )
    assert len(fake.calls) == 1
    assert "productCreate" not in fake.request(0)["query"]
    assert result.shopifyProductId == PRODUCT_GID
    assert result.imageUrl == CDN_URL


def test_handle_owned_by_another_product_is_not_reused():
    other = data(productByIdentifier={"id": PRODUCT_GID, "tags": [], "media": {"nodes": []}})
    fake = FakeShopify(other)
    with pytest.raises(ShopifyError, match="did not create"):
        shopify.create_draft_product(record(), client=make_client(fake))
    assert len(fake.calls) == 1


def test_user_errors_raise():
    rejected = data(
        productCreate={
            "product": None,
            "userErrors": [{"field": ["title"], "message": "Title can't be blank"}],
        }
    )
    fake = FakeShopify(NOT_FOUND, rejected)
    with pytest.raises(ShopifyError, match="productCreate failed: title: Title can't be blank"):
        shopify.create_draft_product(record(), client=make_client(fake))


def test_top_level_graphql_errors_raise():
    fake = FakeShopify((200, {"errors": [{"message": "Field 'x' doesn't exist"}]}))
    with pytest.raises(ShopifyError, match="Field 'x' doesn't exist"):
        make_client(fake).graphql("query { x }")


def test_publish_activates_and_publishes_to_online_store():
    publications = data(
        publications={
            "nodes": [
                {"id": "gid://shopify/Publication/1", "catalog": {"title": "Point of Sale"}},
                {"id": "gid://shopify/Publication/2", "catalog": None},
                {"id": "gid://shopify/Publication/3", "catalog": {"title": "Online Store"}},
            ]
        }
    )
    activated = data(
        productUpdate={"product": {"id": PRODUCT_GID, "status": "ACTIVE"}, "userErrors": []}
    )
    published = data(publishablePublish={"userErrors": []})
    fake = FakeShopify(publications, activated, published)
    draft = record().model_copy(update={"shopifyProductId": PRODUCT_GID})

    assert shopify.publish_product(draft, client=make_client(fake)) == draft
    assert "publications(first: 50, catalogType: APP)" in fake.request(0)["query"]
    assert fake.request(1)["variables"] == {"product": {"id": PRODUCT_GID, "status": "ACTIVE"}}
    assert fake.request(2)["variables"] == {
        "id": PRODUCT_GID,
        "input": [{"publicationId": "gid://shopify/Publication/3"}],
    }


def test_publish_requires_a_shopify_draft():
    fake = FakeShopify()
    with pytest.raises(ShopifyError, match="no Shopify draft"):
        shopify.publish_product(record(), client=make_client(fake))
    assert fake.calls == []


def test_publish_without_online_store_raises_before_writing():
    fake = FakeShopify(data(publications={"nodes": []}))
    draft = record().model_copy(update={"shopifyProductId": PRODUCT_GID})
    with pytest.raises(ShopifyError, match="No Online Store publication"):
        shopify.publish_product(draft, client=make_client(fake))
    assert len(fake.calls) == 1


def test_throttled_requests_retry_once():
    throttled = (200, {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]})
    sleeps = []
    fake = FakeShopify((429, b""), data(shop={"name": "Demo"}), throttled, data(ok=True))
    client = make_client(fake, sleeps)
    assert client.graphql("query { shop { name } }") == {"shop": {"name": "Demo"}}
    assert client.graphql("query { ok }") == {"ok": True}
    assert sleeps == [2.0, 2.0]
    assert len(fake.calls) == 4


def test_throttled_twice_raises():
    fake = FakeShopify((429, b""), (429, b""))
    with pytest.raises(ShopifyError, match="throttled"):
        make_client(fake).graphql("query { shop { name } }")
    assert len(fake.calls) == 2


def test_missing_configuration_raises_only_when_called(monkeypatch):
    for name in ("SHOPIFY_STORE_DOMAIN", "SHOPIFY_ADMIN_ACCESS_TOKEN", "SHOPIFY_API_VERSION"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ShopifyError, match="SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_ACCESS_TOKEN"):
        shopify.create_draft_product(record())


def test_config_from_env_defaults_api_version():
    config = ShopifyConfig.from_env(
        {
            "SHOPIFY_STORE_DOMAIN": "https://demo-shop.myshopify.com/",
            "SHOPIFY_ADMIN_ACCESS_TOKEN": TOKEN,
        }
    )
    assert config.graphql_url == GRAPHQL_URL
    assert TOKEN not in repr(config)


def test_token_never_appears_in_error_text():
    echoed = f'{{"errors": "Invalid access token {TOKEN}"}}'.encode()
    fake = FakeShopify((401, echoed), urllib.error.URLError(f"refused for {TOKEN}"))
    client = make_client(fake)
    with pytest.raises(ShopifyError) as http_error:
        client.graphql("query { shop { name } }")
    with pytest.raises(ShopifyError) as network_error:
        client.graphql("query { shop { name } }")
    assert "HTTP 401" in str(http_error.value)
    for error in (http_error.value, network_error.value):
        assert TOKEN not in str(error)
        assert "[redacted]" in str(error)


def test_upload_image_stages_then_posts_multipart_without_token():
    staged = data(
        stagedUploadsCreate={
            "stagedTargets": [
                {
                    "url": "https://shopify-staged-uploads.storage.googleapis.com/",
                    "resourceUrl": "https://shopify-staged-uploads.storage.googleapis.com/tmp/mug.jpg",
                    "parameters": [{"name": "key", "value": "tmp/mug.jpg"}],
                }
            ],
            "userErrors": [],
        }
    )
    fake = FakeShopify(staged, (204, b""))
    resource_url = shopify.upload_image(
        b"JPEGBYTES", "mug.jpg", "image/jpeg", client=make_client(fake)
    )

    assert resource_url == "https://shopify-staged-uploads.storage.googleapis.com/tmp/mug.jpg"
    assert fake.request(0)["variables"] == {
        "input": [
            {
                "resource": "IMAGE",
                "filename": "mug.jpg",
                "mimeType": "image/jpeg",
                "httpMethod": "POST",
            }
        ]
    }
    upload = fake.calls[1]
    assert upload["url"] == "https://shopify-staged-uploads.storage.googleapis.com/"
    assert "X-Shopify-Access-Token" not in upload["headers"]
    assert upload["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    body = upload["body"]
    assert b'name="key"\r\n\r\ntmp/mug.jpg\r\n' in body
    assert (
        b'name="file"; filename="mug.jpg"\r\nContent-Type: image/jpeg\r\n\r\nJPEGBYTES\r\n' in body
    )
    assert body.index(b'name="key"') < body.index(b'name="file"')


def test_failed_staged_upload_raises():
    staged = data(
        stagedUploadsCreate={
            "stagedTargets": [
                {"url": "https://up.example/", "resourceUrl": "https://r", "parameters": []}
            ],
            "userErrors": [],
        }
    )
    fake = FakeShopify(staged, (403, b"denied"))
    with pytest.raises(ShopifyError, match="HTTP 403"):
        shopify.upload_image(b"x", "mug.jpg", "image/jpeg", client=make_client(fake))


def test_product_handle_is_stable_and_url_safe():
    assert shopify.product_handle("Demo Mug_001") == "commercecrew-demo-mug-001"
    with pytest.raises(ShopifyError):
        shopify.product_handle("---")
