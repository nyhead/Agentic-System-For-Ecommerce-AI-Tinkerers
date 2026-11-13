"""Shared fixture contracts. Nihad coordinates changes to this file."""

from decimal import Decimal
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ProductRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productId: str = Field(min_length=1)
    shopifyProductId: str | None = None
    title: str = Field(min_length=1)
    description: str
    imageUrl: str | None = None
    # In the shop's currency, without a symbol; None when the merchant has not stated a price.
    price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    productType: str | None = None
    vendor: str | None = None
    tags: list[str] = Field(default_factory=list)


class SocialAd(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    channel: str
    text: str = Field(alias="copy")


class MarketingResult(BaseModel):
    seoTitle: str
    metaDescription: str
    socialAds: list[SocialAd] = Field(min_length=2, max_length=2)


class MetricsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productId: str
    source: Literal["synthetic", "observed"]
    periodDays: int = Field(gt=0)
    sessions: int = Field(ge=0)
    orders: int = Field(ge=0)


RecommendationRule = Literal[
    "no_traffic", "low_traffic", "no_orders", "low_conversion", "healthy_conversion"
]


class AnalyticsResult(BaseModel):
    """Metrics, conversion rate and one recommended next action for a product."""

    model_config = ConfigDict(extra="forbid")

    productId: str
    source: Literal["synthetic", "observed"]
    periodDays: int = Field(gt=0)
    sessions: int = Field(ge=0)
    orders: int = Field(ge=0)
    # None when sessions are zero: there is no rate to show.
    conversionRate: float | None = Field(ge=0)
    recommendationRule: RecommendationRule
    recommendation: str = Field(min_length=1)


class WorkflowState(TypedDict, total=False):
    product: ProductRecord
    marketing: MarketingResult
    metrics: MetricsRecord
    analytics: AnalyticsResult
    approval: Literal["pending", "approved", "rejected"]
    error: str


# ---------------------------------------------------------------------------
# Market research and pricing (app/agents/research.py). Values the evidence does not
# support stay None; the report never invents a number to fill a field.
# ---------------------------------------------------------------------------

PricingGoal = Literal["faster_sale", "balanced", "preserve_margin"]
ResearchStatus = Literal[
    "queued",
    "finding_comparables",
    "checking_matches",
    "analyzing_prices",
    "preparing_improvements",
    "ready_for_review",
    "needs_information",
    "partial_results",
    "failed",
]
# asking: a current fixed price. auction_bid: a live bid, not a price. sold: a completed sale
# whose final amount the source shows. unknown: e.g. a best offer accepted at a hidden amount.
PriceType = Literal["asking", "auction_bid", "sold", "unknown"]
MatchLevel = Literal["same_model", "related"]
ConfidenceLabel = Literal["low", "medium", "high"]
# Listing URLs come from third-party marketplaces and the cockpit renders them as links. Only
# http(s) passes, so a scraped listing cannot put a javascript: or data: URL in the browser.
ListingUrl = Annotated[str, Field(pattern=r"^(?i:https?)://\S", max_length=2048)]


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProductFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)


class CostInputs(BaseModel):
    """Merchant-supplied costs per unit, in one currency. Missing values stay unknown."""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    unitCost: float | None = Field(default=None, ge=0)
    sellingCostsFixed: float | None = Field(default=None, ge=0)
    sellingFeeRate: float | None = Field(default=None, ge=0, lt=1)
    minMarginRate: float | None = Field(default=None, ge=0, lt=1)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productId: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    category: str | None = None
    confirmedFacts: list[ProductFact] = []
    # Read from photos (e.g. dial text). Used to search, never stated as fact in copy.
    imageSuggestions: list[ProductFact] = []
    missingAttributes: list[str] = []
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    currentPrice: Money | None = None
    costs: CostInputs | None = None
    goal: PricingGoal = "balanced"


class Comparable(BaseModel):
    ref: str
    source: str
    actorId: str
    sourceUrl: ListingUrl
    retrievedAt: str
    listingId: str | None = None
    title: str
    brand: str | None = None
    model: str | None = None
    reference: str | None = None
    specs: dict[str, str] = {}
    condition: str | None = None
    price: Money | None = None
    priceType: PriceType
    shipping: Money | None = None
    shippingNote: str | None = None
    taxNote: str = "Not stated by the source"
    availability: Literal["available", "sold", "unknown"] = "unknown"
    sellerType: str | None = None
    endedAt: str | None = None
    match: MatchLevel | None = None
    matchRationale: str = ""
    differences: list[str] = []
    evidence: dict[str, str] = {}


class ExcludedCandidate(BaseModel):
    ref: str
    sourceUrl: ListingUrl
    title: str
    reason: str


class PriceStats(BaseModel):
    """Descriptive statistics over one currency and one price type; item price, no shipping."""

    currency: str
    priceType: PriceType
    match: MatchLevel
    count: int
    sampleRefs: list[str]
    minimum: float
    lowerQuartile: float | None
    median: float
    upperQuartile: float | None
    maximum: float


class PricingRecommendation(BaseModel):
    status: Literal["recommended", "provisional", "insufficient_evidence"]
    currency: str | None = None
    startingPrice: float | None = None
    rangeLow: float | None = None
    rangeHigh: float | None = None
    basis: str | None = None
    rationale: list[str] = []
    confidence: ConfidenceLabel | None = None
    confidenceReasons: list[str] = []


class ProfitabilityCheck(BaseModel):
    status: Literal["unchecked", "passes", "below_minimum_margin", "floor_above_market_range"]
    assumptions: list[str] = []
    minimumPrice: float | None = None
    marginAtStart: float | None = None
    markupAtStart: float | None = None


class ProposedChange(BaseModel):
    id: str
    # Content and price are approved separately: approving copy never approves a price.
    kind: Literal["content", "price"]
    field: str
    currentValue: str | None
    proposedValue: str
    rationale: str
    evidenceRefs: list[str] = []
    requiresConfirmation: bool


class PositioningHypothesis(BaseModel):
    audience: str
    statement: str
    basis: Literal["observed", "hypothesis"]
    evidenceRefs: list[str] = []


class ObservedTerm(BaseModel):
    term: str
    count: int
    refs: list[str]


class SourceRun(BaseModel):
    source: str
    actorId: str
    apifyRunId: str | None = None
    status: str = "not_started"
    itemCount: int = 0
    estimatedMaxUsd: float
    reportedUsd: float | None = None
    error: str | None = None
    startedAt: str | None = None
    finishedAt: str | None = None


class ResearchUsage(BaseModel):
    estimatedMaxUsd: float
    reportedUsd: float | None = None
    note: str


class ChangeDecision(BaseModel):
    decisionId: str
    kind: Literal["content", "price"]
    changeIds: list[str]
    approved: bool
    outcome: Literal["recorded", "needs_new_review"]
    note: str
    decidedAt: str


class ResearchReport(BaseModel):
    researchId: str
    productId: str
    draftVersion: str
    status: ResearchStatus
    dataMode: Literal["live", "fixture"]
    country: str | None = None
    currency: str | None = None
    goal: PricingGoal = "balanced"
    assumptions: list[str] = []
    queries: list[str] = []
    comparables: list[Comparable] = []
    excluded: list[ExcludedCandidate] = []
    stats: list[PriceStats] = []
    recommendation: PricingRecommendation | None = None
    profitability: ProfitabilityCheck | None = None
    proposedChanges: list[ProposedChange] = []
    specifications: list[ProductFact] = []
    observedTerms: list[ObservedTerm] = []
    positioning: list[PositioningHypothesis] = []
    merchantQuestions: list[str] = []
    limitations: list[str] = []
    sources: list[SourceRun] = []
    usage: ResearchUsage | None = None
    decisions: list[ChangeDecision] = []
    error: str | None = None
    createdAt: str
    updatedAt: str
    completedAt: str | None = None
