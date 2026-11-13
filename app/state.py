"""Shared fixture contracts. Nihad coordinates changes to this file."""

from decimal import Decimal
from typing import Literal, TypedDict

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
