import json
import os
import subprocess
import sys
from decimal import Decimal

import pytest

from app.agents import product
from app.state import ProductRecord
from app.tools.llm import Image, LLMClient, LLMError
from app.tools.shopify import ShopifyClient, ShopifyConfig, ShopifyError

PRODUCT_GID = "gid://shopify/Product/1001"
GRAPHQL_URL = "https://demo-shop.myshopify.com/admin/api/2026-10/graphql.json"
STAGED_URL = "https://shopify-staged-uploads.storage.googleapis.com/"
RESOURCE_URL = "https://shopify-staged-uploads.storage.googleapis.com/tmp/photo.jpg"
CREDENTIAL_VARS = (
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "SHOPIFY_STORE_DOMAIN",
    "SHOPIFY_ADMIN_ACCESS_TOKEN",
)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        return self.responses.pop(0)


def reply(title, description="A round blue mug with a curved handle."):
    content = json.dumps({"title": title, "description": description})
    return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def make_llm(*responses):
    fake = FakeLLM(*responses)
    return LLMClient("https://llm.example/v1", "sk-test", "vision/model", transport=fake), fake


class FakeShopify:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        status, payload = self.responses.pop(0)
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def request(self, index):
        return json.loads(self.calls[index]["body"])


def make_shopify(*responses):
    fake = FakeShopify(*responses)
    config = ShopifyConfig("demo-shop.myshopify.com", "shpat_test")
    return ShopifyClient(config, transport=fake, sleep=lambda _: None), fake


def data(**fields):
    return 200, {"data": fields}


NOT_FOUND = data(productByIdentifier=None)
CREATED = data(
    productCreate={"product": {"id": PRODUCT_GID, "media": {"nodes": []}}, "userErrors": []}
)
STAGED = data(
    stagedUploadsCreate={
        "stagedTargets": [{"url": STAGED_URL, "resourceUrl": RESOURCE_URL, "parameters": []}],
        "userErrors": [],
    }
)


def record(**fields):
    values = {"productId": "demo-mug-001", "title": "Blue Mug", "description": "A blue mug."}
    return ProductRecord(**(values | fields))


def test_draft_from_notes_and_photo():
    llm, fake = make_llm(reply("Blue Ceramic Mug"))
    photo = Image.from_bytes(b"\x89PNG fake", "image/png")

    result = product.draft_listing(
        "demo-mug-001", "  Ceramic, 350 ml, dishwasher safe.  ", image=photo, llm=llm
    )

    assert result == ProductRecord(
        productId="demo-mug-001",
        title="Blue Ceramic Mug",
        description="A round blue mug with a curved handle.",
    )
    assert result.shopifyProductId is None
    (body,) = fake.calls
    system, user = body["messages"]
    assert system == {"role": "system", "content": product.SYSTEM_PROMPT}
    text, image_part = user["content"]
    assert "<notes>\nCeramic, 350 ml, dishwasher safe.\n</notes>" in text["text"]
    assert "photo of the product is attached" in text["text"]
    assert image_part == {"type": "image_url", "image_url": {"url": photo.url}}
    assert body["temperature"] == product.DRAFT_TEMPERATURE


def test_prompt_forbids_inventing_what_a_photo_cannot_show():
    prompt = " ".join(product.SYSTEM_PROMPT.split())
    for phrase in (
        "under their own name",
        "shape, colour and visible features, and nothing else",
        "made of",
        "price or stock",
        "State such facts only when the owner's notes state them",
        "follow the notes",
        "Do not guess",
        "no markdown",
        "emoji",
        f"at most {product.MAX_TITLE_LENGTH}",
    ):
        assert phrase in prompt


def test_prompt_allows_a_price_only_from_the_notes():
    prompt = " ".join(product.SYSTEM_PROMPT.split())
    for phrase in (
        "only when the owner's notes state a price",
        "A photo never shows a price",
        "Never estimate, suggest or round a price",
        "legibly written on the product",
        f"up to {product.MAX_TAGS} short, lowercase",
        "No invented attributes",
    ):
        assert phrase in prompt


def fields_reply(**fields):
    content = json.dumps({"title": "Blue Mug", "description": "A blue mug."} | fields)
    return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def test_draft_keeps_stated_price_type_vendor_and_tags():
    llm, _ = make_llm(
        fields_reply(price="24.90", productType="Mug", vendor="Acme", tags=["Blue", "ceramic"])
    )
    result = product.draft_listing("p", "Acme mug, 24.90", llm=llm)
    assert result.price == Decimal("24.90")
    assert result.productType == "Mug"
    assert result.vendor == "Acme"
    assert result.tags == ["blue", "ceramic"]


@pytest.mark.parametrize(
    "value", ["$24.90", "about 25", "24,90", "1e3", "-5", "24.999", True, [24]]
)
def test_price_that_is_not_a_plain_amount_is_dropped(value):
    llm, fake = make_llm(fields_reply(price=value))
    result = product.draft_listing("p", "notes", llm=llm)
    assert result.price is None
    assert result.title == "Blue Mug"
    assert len(fake.calls) == 1


def test_numeric_price_is_kept_exactly():
    llm, _ = make_llm(fields_reply(price=24.9))
    assert product.draft_listing("p", "notes", llm=llm).price == Decimal("24.9")


def test_missing_fields_stay_empty():
    llm, _ = make_llm(fields_reply(price=None, productType="  ", vendor=None))
    result = product.draft_listing("p", "notes", llm=llm)
    assert (result.price, result.productType, result.vendor, result.tags) == (None, None, None, [])


def test_tags_are_capped_in_count_and_length():
    tags = ["blue", "BLUE", "x" * (product.MAX_TAG_LENGTH + 1), " "] + [f"t{i}" for i in range(20)]
    llm, _ = make_llm(fields_reply(tags=tags))
    result = product.draft_listing("p", "notes", llm=llm).tags
    assert len(result) == product.MAX_TAGS
    assert result[:2] == ["blue", "t0"]
    assert all(len(tag) <= product.MAX_TAG_LENGTH for tag in result)


def test_draft_from_notes_only_sends_text():
    llm, fake = make_llm(reply("Blue Mug"))
    result = product.draft_listing("demo-mug-001", "Blue mug, 350 ml.", llm=llm)
    assert result.title == "Blue Mug"
    user = fake.calls[0]["messages"][1]["content"]
    assert isinstance(user, str)
    assert "There is no photo" in user
    assert "Blue mug, 350 ml." in user


def test_draft_from_photo_only_says_there_are_no_notes():
    llm, fake = make_llm(reply("Blue Mug"))
    product.draft_listing(
        "demo-mug-001", " ", image=Image.from_url("https://x.example/m.jpg"), llm=llm
    )
    text = fake.calls[0]["messages"][1]["content"][0]["text"]
    assert "gave no notes" in text
    assert "<notes>" not in text


def test_nothing_to_draft_from_raises_without_calling_the_model():
    llm, fake = make_llm()
    with pytest.raises(ValueError, match="nothing to draft from"):
        product.draft_listing("demo-mug-001", "   ", llm=llm)
    assert fake.calls == []


def test_over_long_title_is_cut_at_a_word():
    long_title = "Handmade Blue Ceramic Coffee Mug with Glazed Finish and Easy Grip Handle for Tea"
    llm, _ = make_llm(reply(long_title))
    title = product.draft_listing("demo-mug-001", "notes", llm=llm).title
    assert len(title) <= product.MAX_TITLE_LENGTH
    assert long_title.startswith(title)
    assert not title.endswith(" ")
    assert long_title[len(title)] == " "


def test_title_without_spaces_is_cut_hard():
    llm, _ = make_llm(reply("x" * 300))
    assert product.draft_listing("p", "notes", llm=llm).title == "x" * product.MAX_TITLE_LENGTH


def test_blank_model_field_is_sent_back_for_correction():
    llm, fake = make_llm(reply("   "), reply("Blue Mug"))
    assert product.draft_listing("p", "notes", llm=llm).title == "Blue Mug"
    assert len(fake.calls) == 2


def test_model_failure_propagates_as_llm_error():
    llm, _ = make_llm((503, b"down"), (503, b"down"))
    with pytest.raises(LLMError, match="HTTP 503"):
        product.draft_listing("demo-mug-001", "notes", llm=llm)


def test_default_llm_is_built_at_call_time_with_shorter_timeout(monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(LLMError, match="not configured"):
        product.draft_listing("demo-mug-001", "notes")
    assert product.DRAFT_TIMEOUT_SECONDS < 60.0


def test_create_draft_with_image_url():
    client, fake = make_shopify(NOT_FOUND, CREATED)
    result = product.create_draft(record(), image_url="https://example.com/m.jpg", client=client)
    assert len(fake.calls) == 2
    media = fake.request(1)["variables"]["media"]
    assert media[0]["originalSource"] == "https://example.com/m.jpg"
    assert result.shopifyProductId == PRODUCT_GID
    assert result.imageUrl == "https://example.com/m.jpg"


def test_create_draft_with_photo_uploads_then_uses_resource_url():
    client, fake = make_shopify(STAGED, (204, b""), NOT_FOUND, CREATED)
    result = product.create_draft(
        record(),
        photo=b"JPEGBYTES",
        photo_filename="mug.png",
        photo_mime_type="image/png",
        client=client,
    )
    assert [call["url"] for call in fake.calls] == [GRAPHQL_URL, STAGED_URL] + [GRAPHQL_URL] * 2
    assert fake.request(0)["variables"]["input"][0]["filename"] == "mug.png"
    assert fake.request(0)["variables"]["input"][0]["mimeType"] == "image/png"
    assert b"JPEGBYTES" in fake.calls[1]["body"]
    assert fake.request(3)["variables"]["media"][0]["originalSource"] == RESOURCE_URL
    assert result.shopifyProductId == PRODUCT_GID
    assert result.imageUrl == RESOURCE_URL


def test_create_draft_with_url_and_photo_is_rejected_before_any_request():
    client, fake = make_shopify()
    with pytest.raises(ValueError, match="not both"):
        product.create_draft(
            record(), image_url="https://example.com/m.jpg", photo=b"x", client=client
        )
    assert fake.calls == []


def test_create_draft_for_existing_product_skips_the_photo_upload():
    existing = data(
        productByIdentifier={"id": PRODUCT_GID, "tags": ["commercecrew"], "media": {"nodes": []}}
    )
    client, fake = make_shopify(existing)
    draft = record(shopifyProductId=PRODUCT_GID, imageUrl=RESOURCE_URL)
    assert product.create_draft(draft, photo=b"JPEGBYTES", client=client) == draft
    assert len(fake.calls) == 1
    assert "productByIdentifier" in fake.request(0)["query"]


def test_shopify_failure_propagates():
    rejected = data(productCreate={"product": None, "userErrors": [{"message": "Bad title"}]})
    client, _ = make_shopify(NOT_FOUND, rejected)
    with pytest.raises(ShopifyError, match="Bad title"):
        product.create_draft(record(), client=client)


def test_default_shopify_client_is_built_at_call_time(monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ShopifyError, match="not configured"):
        product.create_draft(record())


def test_publish_activates_and_publishes():
    # Shaped like the real dev store's reply; the Online Store is found by its app handle.
    catalog = {
        "title": "Online Store",
        "apps": {"nodes": [{"handle": "online_store"}]},
    }
    publications = data(
        publications={"nodes": [{"id": "gid://shopify/Publication/3", "catalog": catalog}]}
    )
    activated = data(
        productUpdate={"product": {"id": PRODUCT_GID, "status": "ACTIVE"}, "userErrors": []}
    )
    client, fake = make_shopify(
        publications, data(publishablePublish={"userErrors": []}), activated
    )
    draft = record(shopifyProductId=PRODUCT_GID)

    assert product.publish(draft, client=client) == draft
    assert "publishablePublish" in fake.request(1)["query"]
    assert fake.request(1)["variables"]["id"] == PRODUCT_GID
    assert "productUpdate" in fake.request(2)["query"]
    assert fake.request(2)["variables"] == {"product": {"id": PRODUCT_GID, "status": "ACTIVE"}}


def test_publish_failure_propagates():
    client, fake = make_shopify()
    with pytest.raises(ShopifyError, match="no Shopify draft"):
        product.publish(record(), client=client)
    assert fake.calls == []


def test_import_needs_no_credentials():
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_VARS}
    subprocess.run([sys.executable, "-c", "import app.agents.product"], env=env, check=True)
