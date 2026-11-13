"""Market research routes: start research, read its progress, and decide on its proposals.

Research runs in the background; POST returns at once and the cockpit polls GET. Starting the
same research again returns the existing job, so a refresh or reconnect never pays twice.
Deciding on proposals only records the decision: nothing here writes to Shopify.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app import graph
from app.agents import research
from app.state import (
    CostInputs,
    Money,
    PricingGoal,
    ProductFact,
    ResearchReport,
    ResearchRequest,
)

router = APIRouter(prefix="/api", tags=["research"])


class ResearchStartRequest(BaseModel):
    """The product as the cockpit knows it. Empty title or description are filled from the
    listing workflow when it has this product."""

    title: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=5000)
    category: str | None = Field(default=None, max_length=120)
    confirmedFacts: list[ProductFact] = Field(default=[], max_length=40)
    imageSuggestions: list[ProductFact] = Field(default=[], max_length=40)
    missingAttributes: list[str] = Field(default=[], max_length=40)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    currentPrice: Money | None = None
    costs: CostInputs | None = None
    goal: PricingGoal = "balanced"
    # True starts a new paid collection even when a fresh result is cached.
    refresh: bool = False


class DecisionRequest(BaseModel):
    kind: Literal["content", "price"]
    changeIds: list[str] = Field(min_length=1, max_length=20)
    approved: bool
    # The product's fields as the merchant sees them now, for the changed-since-research check.
    # The workflow's own values win when it has this product.
    current: dict[str, str | None] = {}


def get_research() -> research.ResearchService:
    """The process-wide research service; tests override this dependency."""
    return research.default_service()


def get_workflow() -> graph.Workflow:
    return graph.default_workflow()


Service = Annotated[research.ResearchService, Depends(get_research)]
Workflow = Annotated[graph.Workflow, Depends(get_workflow)]


def _workflow_fields(workflow: graph.Workflow, product_id: str) -> dict[str, str | None]:
    state = workflow.snapshot(product_id) or {}
    product = state.get("product")
    if product is None:
        return {}
    return {"title": product.title, "description": product.description}


@router.post(
    "/products/{productId}/research",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Cached research reused"}},
)
def start_research(
    productId: str, body: ResearchStartRequest, service: Service, workflow: Workflow
) -> ResearchReport:
    """Start market research, or return the cached or running job for the same inputs."""
    known = _workflow_fields(workflow, productId)
    fields = body.model_dump(exclude={"refresh"})
    fields["title"] = body.title or known.get("title") or ""
    fields["description"] = body.description or known.get("description") or ""
    request = ResearchRequest.model_validate({**fields, "productId": productId})
    return service.start(request, refresh=body.refresh)


@router.get("/products/{productId}/research")
def latest_research(productId: str, service: Service) -> ResearchReport:
    report = service.latest(productId)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No research for {productId!r}")
    return report


@router.get("/research/{researchId}")
def get_research_report(researchId: str, service: Service) -> ResearchReport:
    try:
        return service.get(researchId)
    except research.UnknownResearchError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/research/{researchId}/decisions")
def decide_changes(
    researchId: str, body: DecisionRequest, service: Service, workflow: Workflow
) -> ResearchReport:
    """Approve or reject content changes or a price change; one kind per request.

    409 when the product changed after research: nothing is approved until the merchant
    reviews again. Repeating an identical decision returns the first one unchanged.
    """
    try:
        report = service.get(researchId)
        current = {**body.current, **_workflow_fields(workflow, report.productId)}
        return service.decide(
            researchId,
            kind=body.kind,
            change_ids=body.changeIds,
            approved=body.approved,
            current=current,
        )
    except research.UnknownResearchError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except research.StaleDraftError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
