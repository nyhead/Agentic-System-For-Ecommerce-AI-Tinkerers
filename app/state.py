"""Shared fixture contracts. Nihad coordinates changes to this file."""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ProductRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productId: str = Field(min_length=1)
    shopifyProductId: str | None = None
    title: str = Field(min_length=1)
    description: str
    imageUrl: str | None = None


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


class WorkflowState(TypedDict, total=False):
    product: ProductRecord
    marketing: MarketingResult
    metrics: MetricsRecord
    approval: Literal["pending", "approved", "rejected"]
    error: str
