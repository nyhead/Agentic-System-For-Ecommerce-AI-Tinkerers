import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app import graph
from app.graph import (
    MissingPriceError,
    NotAwaitingApprovalError,
    UnknownWorkflowError,
    Workflow,
    WorkflowExistsError,
)
from app.state import MetricsRecord
from app.tools import lessons
from app.tools.llm import LLMClient
from app.tools.shopify import ShopifyClient, ShopifyConfig


@pytest.fixture(autouse=True)
def lessons_path(tmp_path, monkeypatch):
    # Never write to the real lessons file; each test starts with an empty memory.
    path = tmp_path / "lessons.json"
    monkeypatch.setenv("LESSONS_PATH", str(path))
    return path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ID = "demo-mug-001"
PRODUCT_GID = "gid://shopify/Product/1001"
PUBLICATION_GID = "gid://shopify/Publication/7"
STAGED_URL = "https://shopify-staged-uploads.storage.googleapis.com/"
RESOURCE_URL = "https://shopify-staged-uploads.storage.googleapis.com/tmp/photo.jpg"
PHOTO = b"\xff\xd8\xff fake jpeg"

LISTING = {"title": "Blue Ceramic Mug", "description": "A blue ceramic mug with a handle."}
PRICED_LISTING = {**LISTING, "price": "24.90"}
VARIANT_GID = "gid://shopify/ProductVariant/2001"
COPY = {
    "seoTitle": "Blue Ceramic Mug",
    "metaDescription": "A blue ceramic mug with a handle.",
    "socialAds": [
        {"channel": "Instagram", "copy": "Deep blue and glazed."},
        {"channel": "Facebook", "copy": "A handle that fits your hand."},
    ],
}
METRICS = MetricsRecord(
    productId=PRODUCT_ID, source="synthetic", periodDays=7, sessions=240, orders=6
)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(json.loads(body))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeShopify:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"url": url, "body": body})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        status, payload = response
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def queue(self, *responses):
        self.responses.extend(responses)

    def operations(self):
        names = []
        for call in self.calls:
            if call["url"] == STAGED_URL:
                names.append("upload")
                continue
            query = json.loads(call["body"])["query"]
            names.append(query.split()[1].split("(")[0])
        return names


def reply(payload):
    content = json.dumps(payload)
    return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def data(**fields):
    return 200, {"data": fields}


def user_errors(name, message):
    return data(**{name: {"userErrors": [{"field": ["x"], "message": message}]}})


NOT_FOUND = data(productByIdentifier=None)
CREATED = data(
    productCreate={
        "product": {
            "id": PRODUCT_GID,
            "variants": {"nodes": [{"id": VARIANT_GID}]},
            "media": {"nodes": [{"preview": {"image": {"url": "https://cdn.shopify.com/m.jpg"}}}]},
        },
        "userErrors": [],
    }
)


def price_set(price="24.90"):
    return data(
        productVariantsBulkUpdate={
            "productVariants": [{"id": VARIANT_GID, "price": price}],
            "userErrors": [],
        }
    )


# Shopify still has the default 0.00 when the publish flow reads the variant.
FACTS = data(
    shop={"currencyCode": "EUR"},
    product={
        "status": "DRAFT",
        "publishedOnPublication": False,
        "variants": {"nodes": [{"id": VARIANT_GID, "price": "0.00", "availableForSale": True}]},
    },
)
STAGED = data(
    stagedUploadsCreate={
        "stagedTargets": [{"url": STAGED_URL, "resourceUrl": RESOURCE_URL, "parameters": []}],
        "userErrors": [],
    }
)
UPLOADED = (204, b"")
PUBLICATIONS = data(
    publications={
        "nodes": [
            {"id": PUBLICATION_GID, "catalog": {"apps": {"nodes": [{"handle": "online_store"}]}}}
        ]
    }
)
ACTIVATED = data(
    productUpdate={"product": {"id": PRODUCT_GID, "status": "ACTIVE"}, "userErrors": []}
)
PUBLISHED = data(publishablePublish={"userErrors": []})
PUBLISH_FLOW = (PUBLICATIONS, FACTS, price_set(), PUBLISHED, ACTIVATED)


class Abort(BaseException):
    """Stands in for the process being interrupted in the middle of a step."""


@pytest.fixture(autouse=True)
def no_posthog_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_API_KEY", raising=False)


class Captured:
    def __init__(self):
        self.calls = []

    def __call__(self, event, distinct_id, properties, at):
        self.calls.append((event, distinct_id, properties, at))

    def names(self):
        return [call[0] for call in self.calls]


def make_workflow(llm_responses=(), shopify_responses=(), capture=None):
    llm_fake = FakeLLM(*llm_responses)
    shopify_fake = FakeShopify(*shopify_responses)
    llm = LLMClient(
        "https://llm.example/v1", "sk-test", "vision/model", transport=llm_fake, sleep=lambda _: 0
    )
    shopify = ShopifyClient(
        ShopifyConfig("demo-shop.myshopify.com", "shpat_test"),
        transport=shopify_fake,
        sleep=lambda _: None,
    )
    return Workflow(llm=llm, shopify=shopify, capture=capture), llm_fake, shopify_fake


def pending_workflow(metrics=None, listing=PRICED_LISTING):
    created = [NOT_FOUND, CREATED] + ([price_set()] if "price" in listing else [])
    workflow, llm, shopify = make_workflow([reply(listing), reply(COPY)], created)
    state = workflow.start(PRODUCT_ID, "Blue mug, 350 ml, 24.90.", metrics=metrics)
    assert state["approval"] == "pending"
    return workflow, llm, shopify


def actions(state):
    return [(event["agent"], event["action"], event["ok"]) for event in state["events"]]


def test_photo_run_stops_pending_with_draft_copy_and_analytics():
    workflow, llm, shopify = make_workflow(
        [reply(LISTING), reply(COPY)], [STAGED, UPLOADED, NOT_FOUND, CREATED]
    )

    state = workflow.start(
        PRODUCT_ID, "Blue mug, 350 ml.", photo=PHOTO, photo_mime_type="image/jpeg", metrics=METRICS
    )

    assert state["approval"] == "pending"
    assert "error" not in state
    assert "photo" not in state
    assert state["product"].shopifyProductId == PRODUCT_GID
    assert state["product"].title == "Blue Ceramic Mug"
    assert state["product"].imageUrl == "https://cdn.shopify.com/m.jpg"
    assert state["marketing"].seoTitle == "Blue Ceramic Mug"
    assert state["analytics"].conversionRate == 0.025
    assert state["analytics"].recommendation.startswith("Synthetic sample data")
    # The photo goes to the model and, as the staged upload, to the Shopify draft.
    image_part = llm.calls[0]["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert shopify.operations() == [
        "StageImageUpload",
        "upload",
        "FindProductByHandle",
        "CreateDraftProduct",
    ]
    assert PHOTO in shopify.calls[1]["body"]
    create = json.loads(shopify.calls[3]["body"])["variables"]
    assert create["product"]["status"] == "DRAFT"
    assert create["media"][0]["originalSource"] == RESOURCE_URL


def test_notes_only_run_has_no_analytics_and_logs_the_skip():
    workflow, llm, shopify = make_workflow([reply(LISTING), reply(COPY)], [NOT_FOUND, CREATED])

    state = workflow.start(PRODUCT_ID, "Blue mug, 350 ml.")

    assert state["approval"] == "pending"
    assert "analytics" not in state and "metrics" not in state
    assert isinstance(llm.calls[0]["messages"][1]["content"], str)
    assert shopify.operations() == ["FindProductByHandle", "CreateDraftProduct"]
    assert "media" not in json.loads(shopify.calls[1]["body"])["variables"]
    assert actions(state) == [
        ("product", "Wrote a draft title and description", True),
        ("product", "Created the draft in Shopify, hidden from customers", True),
        ("marketing", "Wrote an SEO title, a meta description and two social ads", True),
        ("analytics", "Skipped: no shop numbers for this product yet", True),
        ("workflow", "Waiting for the shop owner to approve or reject", True),
    ]


def test_image_url_is_shown_to_the_model_and_attached_to_the_draft():
    workflow, llm, shopify = make_workflow([reply(LISTING), reply(COPY)], [NOT_FOUND, CREATED])
    url = "https://images.example/mug.jpg"

    workflow.start(PRODUCT_ID, "", image_url=url)

    assert llm.calls[0]["messages"][1]["content"][1]["image_url"]["url"] == url
    create = json.loads(shopify.calls[1]["body"])["variables"]
    assert create["media"][0]["originalSource"] == url


def test_event_log_with_metrics_and_approval_is_ordered_and_json_friendly():
    workflow, _, shopify = pending_workflow(metrics=METRICS)
    shopify.queue(*PUBLISH_FLOW)

    state = workflow.decide(PRODUCT_ID, True)

    assert actions(state) == [
        ("product", "Wrote a draft title and description", True),
        ("product", "Created the draft in Shopify, hidden from customers", True),
        ("marketing", "Wrote an SEO title, a meta description and two social ads", True),
        ("analytics", "Recommended a next step from synthetic metrics", True),
        ("workflow", "Waiting for the shop owner to approve or reject", True),
        ("merchant", "Approved the listing", True),
        ("product", "Published the product to the Online Store", True),
    ]
    for event in state["events"]:
        assert set(event) == {"agent", "action", "ok", "at"}
        assert datetime.fromisoformat(event["at"]).tzinfo is not None
    json.dumps(state["events"])


def test_approval_publishes_and_ends_approved():
    workflow, _, shopify = pending_workflow()
    shopify.queue(*PUBLISH_FLOW)

    state = workflow.decide(PRODUCT_ID, True)

    assert state["approval"] == "approved"
    assert "error" not in state
    # Publish to the channel first and set ACTIVE last, so a failure in between stays hidden.
    # The price is written to Shopify before the product goes live.
    assert shopify.operations()[-5:] == [
        "AppPublications",
        "ProductPurchaseFacts",
        "SetDefaultVariantPrice",
        "PublishProduct",
        "ActivateProduct",
    ]
    priced = json.loads(shopify.calls[-3]["body"])["variables"]
    assert priced == {"productId": PRODUCT_GID, "variants": [{"id": VARIANT_GID, "price": "24.90"}]}
    publish = json.loads(shopify.calls[-2]["body"])["variables"]
    assert publish == {"id": PRODUCT_GID, "input": [{"publicationId": PUBLICATION_GID}]}
    activate = json.loads(shopify.calls[-1]["body"])["variables"]
    assert activate == {"product": {"id": PRODUCT_GID, "status": "ACTIVE"}}
    assert workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}}).next == ()


def test_rejection_publishes_nothing_and_ends_rejected():
    workflow, _, shopify = pending_workflow()
    before = len(shopify.calls)

    state = workflow.decide(PRODUCT_ID, False)

    assert state["approval"] == "rejected"
    assert len(shopify.calls) == before
    assert actions(state)[-1] == ("merchant", "Rejected the listing; nothing was published", True)
    assert workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}}).next == ()


def test_publish_is_reachable_only_from_the_approval_decision():
    workflow, _, shopify = pending_workflow()

    edges = workflow.graph.get_graph().edges
    assert {edge.source for edge in edges if edge.target == "publish"} == {"decision"}
    assert workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}}).next == (
        "decision",
    )
    # Starting again does not move past the approval either.
    with pytest.raises(WorkflowExistsError):
        workflow.start(PRODUCT_ID, "Blue mug, 350 ml.")
    assert "AppPublications" not in shopify.operations()
    assert workflow.snapshot(PRODUCT_ID)["approval"] == "pending"


def test_decide_requires_a_real_bool():
    workflow, _, shopify = pending_workflow()
    with pytest.raises(TypeError):
        workflow.decide(PRODUCT_ID, "yes")
    assert "AppPublications" not in shopify.operations()
    assert workflow.snapshot(PRODUCT_ID)["approval"] == "pending"


def test_model_failure_in_drafting_creates_nothing():
    workflow, _, shopify = make_workflow([(400, b"bad request")])

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["error"] == graph.DRAFT_MODEL_FAILED
    assert "product" not in state and "approval" not in state
    assert shopify.calls == []
    assert actions(state) == [("product", "Could not write the listing", False)]


def test_unusable_input_is_reported_to_the_merchant():
    workflow, llm, shopify = make_workflow()

    state = workflow.start(PRODUCT_ID, "   ")

    assert state["error"] == graph.DRAFT_INPUT_UNUSABLE
    assert llm.calls == [] and shopify.calls == []


def test_shopify_failure_keeps_the_drafted_listing():
    workflow, _, shopify = make_workflow(
        [reply(LISTING)], [NOT_FOUND, user_errors("productCreate", "Title is invalid")]
    )

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["error"] == graph.DRAFT_SHOPIFY_FAILED
    assert state["product"].title == "Blue Ceramic Mug"
    assert state["product"].shopifyProductId is None
    assert "marketing" not in state and "approval" not in state
    assert "shpat_test" not in state["error"]
    assert actions(state) == [
        ("product", "Wrote a draft title and description", True),
        ("product", "Could not save the draft in Shopify", False),
    ]


def test_marketing_failure_keeps_the_shopify_draft():
    workflow, _, _ = make_workflow(
        [reply(LISTING), (500, b"down"), (500, b"down")], [NOT_FOUND, CREATED]
    )

    state = workflow.start(PRODUCT_ID, "Blue mug.", metrics=METRICS)

    assert state["error"] == graph.MARKETING_FAILED
    assert state["product"].shopifyProductId == PRODUCT_GID
    assert "marketing" not in state and "analytics" not in state and "approval" not in state
    assert actions(state)[-1] == ("marketing", "Could not write the launch copy", False)
    with pytest.raises(NotAwaitingApprovalError):
        workflow.decide(PRODUCT_ID, True)


def test_failure_message_is_logged_server_side_without_credentials(caplog):
    workflow, _, _ = make_workflow([(401, b'{"error": "bad key sk-test"}')])
    with caplog.at_level(logging.WARNING, logger="app.graph"):
        state = workflow.start(PRODUCT_ID, "Blue mug.")
    assert "HTTP 401" in caplog.text
    assert "sk-test" not in caplog.text
    assert "HTTP" not in state["error"]


def test_start_again_while_pending_raises_and_changes_nothing():
    workflow, llm, shopify = pending_workflow()
    first = workflow.snapshot(PRODUCT_ID)
    calls = (len(llm.calls), len(shopify.calls))

    with pytest.raises(WorkflowExistsError, match="approval: pending"):
        workflow.start(PRODUCT_ID, "Different notes.", photo=PHOTO, metrics=METRICS)

    assert workflow.snapshot(PRODUCT_ID) == first
    assert (len(llm.calls), len(shopify.calls)) == calls


@pytest.mark.parametrize("approved, status", [(True, "approved"), (False, "rejected")])
def test_start_again_after_a_decision_raises(approved, status):
    workflow, _, shopify = pending_workflow()
    shopify.queue(*PUBLISH_FLOW)
    workflow.decide(PRODUCT_ID, approved)
    with pytest.raises(WorkflowExistsError, match=f"approval: {status}"):
        workflow.start(PRODUCT_ID, "Blue mug.")


def test_start_again_after_a_marketing_failure_resumes_without_a_second_draft():
    workflow, llm, shopify = make_workflow(
        [reply(LISTING), (500, b"down"), (500, b"down")], [NOT_FOUND, CREATED]
    )
    workflow.start(PRODUCT_ID, "Blue mug.")
    llm.responses.append(reply(COPY))

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["approval"] == "pending"
    assert "error" not in state
    assert state["product"].shopifyProductId == PRODUCT_GID
    assert state["marketing"].seoTitle == "Blue Ceramic Mug"
    # One draft call and two failed copy calls, then only the copy call on the retry.
    assert len(llm.calls) == 4
    assert shopify.operations() == ["FindProductByHandle", "CreateDraftProduct"]
    assert [a for a in actions(state) if not a[2]] == [
        ("marketing", "Could not write the launch copy", False)
    ]
    assert actions(state)[-1][0] == "workflow"


def test_start_again_after_a_shopify_failure_reuses_the_written_listing():
    workflow, llm, shopify = make_workflow(
        [reply(LISTING), reply(COPY)], [NOT_FOUND, user_errors("productCreate", "Busy")]
    )
    workflow.start(PRODUCT_ID, "Blue mug.")
    shopify.queue(NOT_FOUND, CREATED)

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["approval"] == "pending"
    assert state["product"].shopifyProductId == PRODUCT_GID
    # The model wrote the listing once; the retry only saved it in Shopify.
    assert len(llm.calls) == 2
    created = [json.loads(c["body"]) for c in shopify.calls if "productCreate" in str(c["body"])]
    assert all(c["variables"]["product"]["title"] == "Blue Ceramic Mug" for c in created)


def test_publish_failure_leaves_a_hidden_draft_and_can_be_retried():
    workflow, _, shopify = pending_workflow()
    shopify.queue(PUBLICATIONS, FACTS, price_set(), user_errors("publishablePublish", "No"))

    state = workflow.decide(PRODUCT_ID, True)

    assert state["error"] == graph.PUBLISH_FAILED
    assert state["approval"] == "pending"
    assert "ActivateProduct" not in shopify.operations()
    calls = len(shopify.calls)
    state = workflow.start(PRODUCT_ID, "Blue mug.")
    assert state["approval"] == "pending" and "error" not in state
    assert len(shopify.calls) == calls  # the existing draft and copy are kept
    shopify.queue(*PUBLISH_FLOW)
    assert workflow.decide(PRODUCT_ID, True)["approval"] == "approved"


def test_decide_on_unknown_product_fails():
    workflow, _, shopify = make_workflow()
    with pytest.raises(UnknownWorkflowError):
        workflow.decide("no-such-product", True)
    assert shopify.calls == []


def test_decide_twice_fails_the_second_time_without_publishing_again():
    workflow, _, shopify = pending_workflow()
    shopify.queue(*PUBLISH_FLOW)
    workflow.decide(PRODUCT_ID, True)
    calls = len(shopify.calls)

    with pytest.raises(NotAwaitingApprovalError):
        workflow.decide(PRODUCT_ID, True)
    with pytest.raises(NotAwaitingApprovalError):
        workflow.decide(PRODUCT_ID, False)
    assert len(shopify.calls) == calls
    assert workflow.snapshot(PRODUCT_ID)["approval"] == "approved"


def test_decide_after_rejection_cannot_publish():
    workflow, _, shopify = pending_workflow()
    workflow.decide(PRODUCT_ID, False)
    with pytest.raises(NotAwaitingApprovalError):
        workflow.decide(PRODUCT_ID, True)
    assert "AppPublications" not in shopify.operations()


def test_snapshot_of_known_and_unknown_products():
    workflow, _, _ = pending_workflow()
    state = workflow.snapshot(PRODUCT_ID)
    assert state["approval"] == "pending"
    assert state["product"].productId == PRODUCT_ID
    assert workflow.snapshot("no-such-product") is None


def test_workflows_are_separate_per_product():
    workflow, llm, shopify = pending_workflow()
    assert workflow.snapshot("other-001") is None
    with pytest.raises(UnknownWorkflowError):
        workflow.decide("other-001", True)


def test_start_rejects_metrics_for_another_product_and_two_images():
    workflow, llm, shopify = make_workflow()
    other = METRICS.model_copy(update={"productId": "other-001"})
    with pytest.raises(ValueError, match="metrics"):
        workflow.start(PRODUCT_ID, "Blue mug.", metrics=other)
    with pytest.raises(ValueError, match="not both"):
        workflow.start(PRODUCT_ID, "Blue mug.", photo=PHOTO, image_url="https://x.example/a.jpg")
    assert workflow.snapshot(PRODUCT_ID) is None


def test_checkpoint_restores_state_types_without_unregistered_type_warnings(caplog):
    with caplog.at_level(logging.WARNING):
        workflow, _, _ = pending_workflow(metrics=METRICS)
        workflow.snapshot(PRODUCT_ID)
    assert "unregistered type" not in caplog.text


def test_module_functions_use_one_lazily_built_default_workflow(monkeypatch):
    workflow, _, shopify = make_workflow(
        [reply(PRICED_LISTING), reply(COPY)], [NOT_FOUND, CREATED, price_set()]
    )
    monkeypatch.setattr(graph, "_default", workflow)

    assert graph.start(PRODUCT_ID, "Blue mug.")["approval"] == "pending"
    assert graph.snapshot(PRODUCT_ID)["approval"] == "pending"
    shopify.queue(*PUBLISH_FLOW)
    assert graph.decide(PRODUCT_ID, True)["approval"] == "approved"


def test_default_workflow_without_credentials_reports_an_error(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(graph, "_default", None)

    state = graph.start("fresh-001", "Blue mug.")

    assert state["error"] == graph.DRAFT_MODEL_FAILED
    assert graph.default_workflow() is graph.default_workflow()


def test_import_needs_no_credentials():
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "SHOPIFY_"))
    }
    result = subprocess.run(
        [sys.executable, "-c", "import app.graph as g; assert g._default is None"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_unexpected_exception_in_a_node_fails_the_step_and_can_be_retried(caplog):
    workflow, _, shopify = pending_workflow()
    shopify.queue(PUBLICATIONS, RuntimeError("bug in a transport"))

    with caplog.at_level(logging.WARNING, logger="app.graph"):
        state = workflow.decide(PRODUCT_ID, True)

    assert state["error"] == graph.PUBLISH_FAILED
    assert state["approval"] == "pending"
    assert "RuntimeError" in caplog.text and "Traceback" in caplog.text
    assert workflow.start(PRODUCT_ID, "Blue mug.")["approval"] == "pending"
    shopify.queue(*PUBLISH_FLOW)
    assert workflow.decide(PRODUCT_ID, True)["approval"] == "approved"


def test_null_publications_end_with_an_error_instead_of_a_stuck_run():
    workflow, _, shopify = pending_workflow()
    shopify.queue(data(publications=None))

    state = workflow.decide(PRODUCT_ID, True)

    assert state["error"] == graph.PUBLISH_FAILED
    assert workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}}).next == ()


def test_run_interrupted_mid_step_is_retried_by_start():
    workflow, llm, shopify = make_workflow([reply(LISTING), Abort()], [NOT_FOUND, CREATED])
    with pytest.raises(Abort):
        workflow.start(PRODUCT_ID, "Blue mug.")
    stuck = workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}})
    assert stuck.next == ("marketing",) and "error" not in stuck.values

    llm.responses.append(reply(COPY))
    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["approval"] == "pending"
    assert shopify.operations() == ["FindProductByHandle", "CreateDraftProduct"]


def test_retry_after_failed_create_keeps_the_photo():
    workflow, _, shopify = make_workflow(
        [reply(LISTING), reply(COPY)],
        [STAGED, UPLOADED, NOT_FOUND, user_errors("productCreate", "Busy")],
    )
    workflow.start(PRODUCT_ID, "Blue mug.", photo=PHOTO)
    shopify.queue(STAGED, UPLOADED, NOT_FOUND, CREATED)

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["approval"] == "pending"
    assert PHOTO in shopify.calls[5]["body"]
    create = json.loads(shopify.calls[7]["body"])["variables"]
    assert create["media"][0]["originalSource"] == RESOURCE_URL


def test_retry_keeps_analytics_instead_of_running_it_again():
    workflow, _, shopify = pending_workflow(metrics=METRICS)
    shopify.queue(PUBLICATIONS, FACTS, price_set(), user_errors("publishablePublish", "No"))
    workflow.decide(PRODUCT_ID, True)

    state = workflow.start(PRODUCT_ID, "Blue mug.")

    assert state["analytics"].conversionRate == 0.025
    recommended = [a for a in actions(state) if a[0] == "analytics"]
    assert recommended == [("analytics", "Recommended a next step from synthetic metrics", True)]


def test_products_and_events_list_every_started_workflow():
    workflow, llm, shopify = pending_workflow()
    llm.responses += [reply(LISTING), reply(COPY)]
    shopify.queue(NOT_FOUND, CREATED)
    workflow.start("other-001", "Red mug.")
    with pytest.raises(UnknownWorkflowError):
        workflow.decide("never-started", True)

    assert workflow.products() == [PRODUCT_ID, "other-001"]
    merged = workflow.events()
    assert len(merged) == 10
    assert [e["at"] for e in merged] == sorted(e["at"] for e in merged)
    assert {e["productId"] for e in merged} == {PRODUCT_ID, "other-001"}
    assert set(merged[0]) == {"agent", "action", "ok", "at", "productId"}
    for product_id in workflow.products():
        mine = [(e["agent"], e["action"], e["ok"]) for e in merged if e["productId"] == product_id]
        assert mine == actions(workflow.snapshot(product_id))
    json.dumps(merged)


def test_module_products_and_events_use_the_default_workflow(monkeypatch):
    workflow, _, _ = pending_workflow()
    monkeypatch.setattr(graph, "_default", workflow)
    assert graph.products() == [PRODUCT_ID]
    assert [e["productId"] for e in graph.events()] == [PRODUCT_ID] * 5


def test_full_run_to_approval_sends_one_posthog_event_per_step():
    capture = Captured()
    workflow, _, shopify = make_workflow(
        [reply(PRICED_LISTING), reply(COPY)], [NOT_FOUND, CREATED, price_set()], capture=capture
    )
    workflow.start(PRODUCT_ID, "Blue mug, 350 ml.", metrics=METRICS)
    shopify.queue(*PUBLISH_FLOW)
    state = workflow.decide(PRODUCT_ID, True)

    assert capture.names() == [
        "listing_drafted",
        "shopify_draft_created",
        "marketing_copy_written",
        "analytics_recommended",
        "approval_requested",
        "listing_approved",
        "listing_published",
    ]
    assert {call[1] for call in capture.calls} == {PRODUCT_ID}
    for (_, _, properties, at), event in zip(capture.calls, state["events"], strict=True):
        assert properties == {
            "agent": event["agent"],
            "ok": True,
            "action": event["action"],
            "$process_person_profile": False,
        }
        assert at == event["at"]
    sent = json.dumps(capture.calls)
    assert "Blue Ceramic Mug" not in sent and "350 ml" not in sent


def test_rejection_and_skipped_analytics_have_their_own_posthog_events():
    capture = Captured()
    workflow, _, _ = make_workflow(
        [reply(LISTING), reply(COPY)], [NOT_FOUND, CREATED], capture=capture
    )
    workflow.start(PRODUCT_ID, "Blue mug.")
    workflow.decide(PRODUCT_ID, False)
    assert capture.names()[-3:] == ["analytics_skipped", "approval_requested", "listing_rejected"]


def test_failed_step_sends_the_failure_event():
    capture = Captured()
    workflow, _, _ = make_workflow([(400, b"bad request")], capture=capture)

    workflow.start(PRODUCT_ID, "Blue mug.")

    [(name, distinct_id, properties, _)] = capture.calls
    assert (name, distinct_id) == ("workflow_step_failed", PRODUCT_ID)
    assert properties == {
        "agent": "product",
        "ok": False,
        "action": "Could not write the listing",
        "$process_person_profile": False,
    }


def test_a_capture_that_raises_does_not_change_the_run():
    def broken(*args):
        raise RuntimeError("posthog down")

    workflow, _, _ = make_workflow(
        [reply(LISTING), reply(COPY)], [NOT_FOUND, CREATED], capture=broken
    )
    state = workflow.start(PRODUCT_ID, "Blue mug.")
    assert state["approval"] == "pending" and "error" not in state


def test_rejection_reason_is_a_lesson_for_that_product_only():
    workflow, _, _ = pending_workflow()
    state = workflow.decide(PRODUCT_ID, False, reason="  Never call it  handmade. ")
    assert state["approval"] == "rejected"
    lesson = (
        "The shop owner rejected an earlier draft of this listing and said: "
        '"Never call it handmade."'
    )
    assert lessons.recall("product", scope=PRODUCT_ID) == [lesson]
    # Not shop-wide: other listings never see one item's rejection reason.
    assert lessons.recall("product") == []
    assert lessons.recall("product", scope="another-product") == []
    assert "handmade" not in lessons.prompt_block("product", scope="another-product")


def test_rejection_reason_is_capped():
    workflow, _, _ = pending_workflow()
    workflow.decide(PRODUCT_ID, False, reason="x" * 1000)
    [lesson] = lessons.recall("product", scope=PRODUCT_ID)
    assert "x" * graph.MAX_REASON_LENGTH + '"' in lesson
    assert "x" * (graph.MAX_REASON_LENGTH + 1) not in lesson


def test_decide_without_a_reason_records_nothing():
    workflow, _, _ = pending_workflow()
    assert workflow.decide(PRODUCT_ID, False)["approval"] == "rejected"
    assert lessons.recall("product", scope=PRODUCT_ID) == []


def test_module_decide_passes_the_reason(monkeypatch):
    workflow, _, _ = pending_workflow()
    monkeypatch.setattr(graph, "_default", workflow)
    graph.decide(PRODUCT_ID, False, reason="Too long.")
    assert lessons.recall("product", scope=PRODUCT_ID) == [
        'The shop owner rejected an earlier draft of this listing and said: "Too long."'
    ]


def test_approval_without_any_price_publishes_nothing_and_keeps_waiting():
    workflow, _, shopify = pending_workflow(listing=LISTING)
    assert workflow.snapshot(PRODUCT_ID)["product"].price is None
    before = len(shopify.calls)

    with pytest.raises(MissingPriceError) as raised:
        workflow.decide(PRODUCT_ID, True)

    assert str(raised.value) == graph.MISSING_PRICE
    assert len(shopify.calls) == before
    state = workflow.snapshot(PRODUCT_ID)
    assert state["approval"] == "pending" and "error" not in state
    assert workflow.graph.get_state({"configurable": {"thread_id": PRODUCT_ID}}).next == (
        "decision",
    )
    # A later approval with a price goes through.
    shopify.queue(*PUBLISH_FLOW)
    state = workflow.decide(PRODUCT_ID, True, price="24.90")
    assert state["approval"] == "approved"
    assert state["product"].price == Decimal("24.90")


def test_price_given_on_approval_is_set_before_publishing_and_replaces_the_notes_price():
    workflow, _, shopify = pending_workflow()
    before = len(shopify.calls)
    shopify.queue(PUBLICATIONS, FACTS, price_set("19.50"), PUBLISHED, ACTIVATED)

    state = workflow.decide(PRODUCT_ID, True, price="19.50")

    assert state["approval"] == "approved"
    assert state["product"].price == Decimal("19.50")
    assert workflow.snapshot(PRODUCT_ID)["product"].price == Decimal("19.50")
    assert shopify.operations()[before:] == [
        "AppPublications",
        "ProductPurchaseFacts",
        "SetDefaultVariantPrice",
        "PublishProduct",
        "ActivateProduct",
    ]
    priced = json.loads(shopify.calls[before + 2]["body"])["variables"]
    assert priced["variants"] == [{"id": VARIANT_GID, "price": "19.50"}]


def test_price_from_the_notes_publishes_with_the_two_argument_call():
    workflow, _, shopify = pending_workflow()
    shopify.queue(*PUBLISH_FLOW)

    state = workflow.decide(PRODUCT_ID, True)

    assert state["approval"] == "approved"
    assert state["product"].price == Decimal("24.90")


@pytest.mark.parametrize("price", ["-1", "12.345", "free", "NaN"])
def test_invalid_price_is_rejected_before_anything_is_sent(price):
    workflow, _, shopify = pending_workflow(listing=LISTING)
    before = len(shopify.calls)

    with pytest.raises(ValueError):
        workflow.decide(PRODUCT_ID, True, price=price)

    assert len(shopify.calls) == before
    assert workflow.snapshot(PRODUCT_ID)["approval"] == "pending"
    assert workflow.snapshot(PRODUCT_ID)["product"].price is None


def test_rejection_ignores_a_price():
    workflow, _, shopify = pending_workflow(listing=LISTING)
    before = len(shopify.calls)

    state = workflow.decide(PRODUCT_ID, False, price="nonsense")

    assert state["approval"] == "rejected"
    assert state["product"].price is None
    assert len(shopify.calls) == before
