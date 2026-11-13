"""Analytics route: a product's metrics, conversion rate and one recommended next action."""

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.agents.analytics import analyze, metrics_for_product
from app.state import AnalyticsResult, MetricsRecord

METRICS_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "metrics.json"

router = APIRouter(prefix="/api", tags=["analytics"])


def load_metrics() -> list[MetricsRecord]:
    """Synthetic fixture metrics until PostHog is connected."""
    data = json.loads(METRICS_FIXTURE.read_text(encoding="utf-8"))
    # The fixture is a single object today; accept a list too so more products can be added.
    records = data if isinstance(data, list) else [data]
    return [MetricsRecord.model_validate(record) for record in records]


@router.get("/products/{productId}/analytics")
def product_analytics(
    productId: str,
    records: Annotated[list[MetricsRecord], Depends(load_metrics)],
) -> AnalyticsResult:
    metrics = metrics_for_product(records, productId)
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"No metrics for product {productId}")
    return analyze(metrics)
