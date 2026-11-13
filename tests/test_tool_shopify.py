import json
import urllib.error
from decimal import Decimal

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


VARIANT_GID = "gid://shopify/ProductVariant/2002"
CREATED_WITH_VARIANT = data(
    productCreate={
        "product": {
            "id": PRODUCT_GID,
            "variants": {"nodes": [{"id": VARIANT_GID}]},
            "media": {"nodes": []},
        },
        "userErrors": [],
    }
)
PRICED = data(
    productVariantsBulkUpdate={
        "productVariants": [{"id": VARIANT_GID, "price": "24.90"}],
        "userErrors": [],
    }
)


def listing(**fields):
    values = {"price": "24.90", "productType": "Mug", "vendor": "Acme", "tags": ["blue"]}
    return ProductRecord(**(record().model_dump() | values | fields))


def test_create_draft_sends_type_vendor_tags_and_sets_variant_price():
    fake = FakeShopify(NOT_FOUND, CREATED_WITH_VARIANT, PRICED)
    result = shopify.create_draft_product(listing(), client=make_client(fake))

    product = fake.request(1)["variables"]["product"]
    assert "variants(first: 1)" in fake.request(1)["query"]
    assert product["productType"] == "Mug"
    assert product["vendor"] == "Acme"
    assert product["tags"] == ["commercecrew", "blue"]
    price = fake.request(2)
    assert "productVariantsBulkUpdate(productId: $productId, variants: $variants)" in price["query"]
    assert price["variables"] == {
        "productId": PRODUCT_GID,
        "variants": [{"id": VARIANT_GID, "price": "24.90"}],
    }
    assert result.shopifyProductId == PRODUCT_GID
    assert result.price == listing().price


def test_price_is_sent_with_two_decimals():
    fake = FakeShopify(NOT_FOUND, CREATED_WITH_VARIANT, PRICED)
    shopify.create_draft_product(listing(price=Decimal("1E+1")), client=make_client(fake))
    assert fake.request(2)["variables"]["variants"][0]["price"] == "10.00"


def test_commercecrew_tag_is_not_duplicated():
    fake = FakeShopify(NOT_FOUND, CREATED_WITH_VARIANT, PRICED)
    shopify.create_draft_product(listing(tags=["commercecrew", "blue"]), client=make_client(fake))
    assert fake.request(1)["variables"]["product"]["tags"] == ["commercecrew", "blue"]


def test_no_price_request_without_a_price():
    fake = FakeShopify(NOT_FOUND, CREATED_WITH_VARIANT)
    shopify.create_draft_product(
        listing(price=None, productType=None, vendor=None, tags=[]), client=make_client(fake)
    )
    assert len(fake.calls) == 2
    product = fake.request(1)["variables"]["product"]
    assert "productType" not in product
    assert "vendor" not in product
    assert product["tags"] == ["commercecrew"]


def test_no_price_request_for_an_existing_product():
    existing = data(
        productByIdentifier={"id": PRODUCT_GID, "tags": ["commercecrew"], "media": {"nodes": []}}
    )
    fake = FakeShopify(existing)
    result = shopify.create_draft_product(listing(), client=make_client(fake))
    assert len(fake.calls) == 1
    assert result.shopifyProductId == PRODUCT_GID


def test_failed_price_step_raises_and_says_the_draft_exists():
    rejected = data(
        productVariantsBulkUpdate={
            "productVariants": None,
            "userErrors": [{"field": ["variants", "0", "price"], "message": "Price is invalid"}],
        }
    )
    fake = FakeShopify(NOT_FOUND, CREATED_WITH_VARIANT, rejected)
    with pytest.raises(ShopifyError, match=f"Draft {PRODUCT_GID} was created but its price"):
        shopify.create_draft_product(listing(), client=make_client(fake))
    assert len(fake.calls) == 3


def test_created_product_without_variant_raises_on_price():
    fake = FakeShopify(NOT_FOUND, CREATED)
    with pytest.raises(ShopifyError, match="without a variant"):
        shopify.create_draft_product(listing(), client=make_client(fake))


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
    def catalog(handle):
        return {"apps": {"nodes": [{"handle": handle}]}}

    publications = data(
        publications={
            "nodes": [
                {"id": "gid://shopify/Publication/1", "catalog": catalog("pos")},
                {"id": "gid://shopify/Publication/2", "catalog": None},
                {"id": "gid://shopify/Publication/3", "catalog": catalog("online_store")},
            ]
        }
    )
    activated = data(
        productUpdate={"product": {"id": PRODUCT_GID, "status": "ACTIVE"}, "userErrors": []}
    )
    published = data(publishablePublish={"userErrors": []})
    fake = FakeShopify(publications, published, activated)
    draft = record().model_copy(update={"shopifyProductId": PRODUCT_GID})

    assert shopify.publish_product(draft, client=make_client(fake)) == draft
    assert "publications(first: 50, catalogType: APP)" in fake.request(0)["query"]
    # Publish to the channel first; ACTIVE last, so a failure in between leaves a hidden DRAFT.
    assert "publishablePublish" in fake.request(1)["query"]
    assert fake.request(1)["variables"] == {
        "id": PRODUCT_GID,
        "input": [{"publicationId": "gid://shopify/Publication/3"}],
    }
    assert "productUpdate" in fake.request(2)["query"]
    assert fake.request(2)["variables"] == {"product": {"id": PRODUCT_GID, "status": "ACTIVE"}}


def test_failed_channel_publish_sends_no_status_change():
    publications = data(
        publications={
            "nodes": [
                {
                    "id": "gid://shopify/Publication/3",
                    "catalog": {"apps": {"nodes": [{"handle": "online_store"}]}},
                }
            ]
        }
    )
    failed = data(publishablePublish={"userErrors": [{"field": ["id"], "message": "Nope"}]})
    fake = FakeShopify(publications, failed)
    draft = record().model_copy(update={"shopifyProductId": PRODUCT_GID})
    with pytest.raises(ShopifyError, match="publishablePublish failed"):
        shopify.publish_product(draft, client=make_client(fake))
    assert len(fake.calls) == 2
    assert all("productUpdate" not in json.loads(call["body"])["query"] for call in fake.calls)


@pytest.mark.parametrize("reply", [{"publications": None}, {"publications": {"nodes": None}}, {}])
def test_missing_publications_raise_shopify_error(reply):
    fake = FakeShopify((200, {"data": reply}))
    with pytest.raises(ShopifyError, match="No Online Store publication"):
        shopify.online_store_publication_id(make_client(fake))


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


VARIANT_GID = "gid://shopify/ProductVariant/4242"
ONLINE_STORE = data(
    publications={
        "nodes": [
            {
                "id": "gid://shopify/Publication/3",
                "catalog": {"apps": {"nodes": [{"handle": "online_store"}]}},
            }
        ]
    }
)


def purchase_facts(status="ACTIVE", published=True, available=True, variants=None):
    nodes = (
        [{"id": VARIANT_GID, "price": "24.00", "availableForSale": available}]
        if variants is None
        else variants
    )
    return data(
        shop={"currencyCode": "EUR"},
        product={
            "status": status,
            "publishedOnPublication": published,
            "variants": {"nodes": nodes},
        },
    )


def listed():
    return record().model_copy(update={"shopifyProductId": PRODUCT_GID})


def test_purchase_info_links_active_available_product_to_checkout():
    fake = FakeShopify(ONLINE_STORE, purchase_facts())
    info = shopify.purchase_info(listed(), client=make_client(fake))
    assert info.checkout_url == "https://demo-shop.myshopify.com/cart/4242:1"
    assert info.reason is None
    assert (info.price, info.currency) == ("24.00", "EUR")
    assert info.variant_id == VARIANT_GID
    assert info.status == "ACTIVE" and info.published and info.available
    sent = fake.request(1)
    assert "publishedOnPublication(publicationId: $publicationId)" in sent["query"]
    assert sent["variables"] == {"id": PRODUCT_GID, "publicationId": "gid://shopify/Publication/3"}


def test_purchase_info_uses_given_publication_without_lookup():
    fake = FakeShopify(purchase_facts())
    info = shopify.purchase_info(
        listed(), publication_id="gid://shopify/Publication/9", client=make_client(fake)
    )
    assert info.checkout_url is not None
    assert len(fake.calls) == 1
    assert fake.request(0)["variables"]["publicationId"] == "gid://shopify/Publication/9"


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (purchase_facts(status="DRAFT", published=False), "not_active"),
        (purchase_facts(status="ARCHIVED"), "not_active"),
        (purchase_facts(published=False), "not_published"),
        (purchase_facts(available=False), "sold_out"),
        (purchase_facts(variants=[]), "no_variant"),
        (data(shop={"currencyCode": "EUR"}, product=None), "not_found"),
    ],
)
def test_purchase_info_gives_no_link_when_product_cannot_be_bought(facts, reason):
    fake = FakeShopify(ONLINE_STORE, facts)
    info = shopify.purchase_info(listed(), client=make_client(fake))
    assert info.checkout_url is None
    assert info.reason == reason


def test_sold_out_product_still_reports_price():
    fake = FakeShopify(ONLINE_STORE, purchase_facts(available=False))
    info = shopify.purchase_info(listed(), client=make_client(fake))
    assert (info.price, info.currency, info.available) == ("24.00", "EUR", False)


def test_purchase_info_requires_a_shopify_product():
    fake = FakeShopify()
    with pytest.raises(ShopifyError, match="no Shopify product"):
        shopify.purchase_info(record(), client=make_client(fake))
    assert fake.calls == []
