"""Workflow routes: draft a listing from a photo and notes, then the merchant's decision."""

import base64
import binascii
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app import graph
from app.agents.analytics import metrics_for_product
from app.api.analytics import load_metrics
from app.state import AnalyticsResult, MarketingResult, MetricsRecord, ProductRecord

router = APIRouter(prefix="/api", tags=["workflow"])

MAX_PHOTO_BYTES = 8 * 1024 * 1024
DATA_URL = re.compile(r"^data:(?P<mime>image/[A-Za-z0-9.+-]+);base64,(?P<data>.*)$", re.DOTALL)
PRODUCT_ID = r"^[A-Za-z0-9_-]{1,64}$"


class DraftRequest(BaseModel):
    """Photo and notes in; either may be empty, but not both."""

    notes: str = Field(default="", max_length=4000)
    # The photo as a data URL, as FileReader.readAsDataURL returns it: data:image/jpeg;base64,...
    photo: str | None = None
    # Or a public image URL instead of a photo.
    imageUrl: str | None = None
    # Leave empty and the service picks a new id.
    productId: str | None = Field(default=None, pattern=PRODUCT_ID)


class ApprovalRequest(BaseModel):
    approved: bool


class WorkflowEvent(BaseModel):
    productId: str | None = None
    agent: str
    action: str
    ok: bool
    at: str


class WorkflowView(BaseModel):
    """One product's workflow as the cockpit shows it. Missing parts are null."""

    productId: str
    approval: Literal["pending", "approved", "rejected"] | None = None
    error: str | None = None
    product: ProductRecord | None = None
    marketing: MarketingResult | None = None
    metrics: MetricsRecord | None = None
    analytics: AnalyticsResult | None = None
    events: list[WorkflowEvent] = []


def get_workflow() -> graph.Workflow:
    """The process-wide workflow; tests override this dependency with a stub."""
    return graph.default_workflow()


Workflow = Annotated[graph.Workflow, Depends(get_workflow)]


def _view(product_id: str, state: dict[str, Any]) -> WorkflowView:
    return WorkflowView.model_validate({**state, "productId": product_id})


def _decode_photo(photo: str) -> tuple[bytes, str]:
    match = DATA_URL.match(photo.strip())
    if match is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "photo must be an image data URL: data:image/jpeg;base64,...",
        )
    try:
        data = base64.b64decode(match["data"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "photo is not valid base64"
        ) from error
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "photo is empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "photo is larger than 8 MB")
    return data, match["mime"]


@router.post("/products/draft", status_code=status.HTTP_201_CREATED)
def draft_product(body: DraftRequest, workflow: Workflow) -> WorkflowView:
    """Draft the listing, save it to Shopify hidden from customers, write the launch copy.

    Blocks until the workflow pauses for approval, usually 10 to 20 seconds. A step that fails
    returns 201 with `error` set in words for the merchant; sending the same productId again
    retries it and keeps what already succeeded.
    """
    if not body.notes.strip() and body.photo is None and body.imageUrl is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Add a photo, a few notes, or both"
        )
    if body.photo is not None and body.imageUrl is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Send photo or imageUrl, not both"
        )
    product_id = body.productId or f"product-{uuid.uuid4().hex[:8]}"
    photo, mime_type = _decode_photo(body.photo) if body.photo is not None else (None, "image/jpeg")
    metrics = metrics_for_product(load_metrics(), product_id)
    try:
        state = workflow.start(
            product_id,
            body.notes,
            photo=photo,
            photo_mime_type=mime_type,
            image_url=body.imageUrl,
            metrics=metrics,
        )
    except graph.WorkflowExistsError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return _view(product_id, dict(state))


@router.post("/products/{productId}/approval")
def decide_product(productId: str, body: ApprovalRequest, workflow: Workflow) -> WorkflowView:
    """Approve to publish the listing to the Online Store; reject to leave it a hidden draft."""
    try:
        state = workflow.decide(productId, body.approved)
    except graph.UnknownWorkflowError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except graph.NotAwaitingApprovalError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return _view(productId, dict(state))


@router.get("/products")
def list_products(workflow: Workflow) -> list[WorkflowView]:
    """Every workflow this process started, oldest first. Lost when the service restarts."""
    views = []
    for product_id in workflow.products():
        state = workflow.snapshot(product_id)
        if state is not None:
            views.append(_view(product_id, dict(state)))
    return views


@router.get("/products/{productId}")
def get_product(productId: str, workflow: Workflow) -> WorkflowView:
    state = workflow.snapshot(productId)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No workflow for product {productId!r}")
    return _view(productId, dict(state))


@router.get("/events")
def list_events(workflow: Workflow) -> list[WorkflowEvent]:
    """The action log of every workflow, oldest first: which agent acted and whether it worked."""
    return [WorkflowEvent.model_validate(event) for event in workflow.events()]
