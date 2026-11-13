"""Demo sale: record a Shopify test order for a published product; no real payment is taken."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.sales import SimulatedSale, record_test_sale
from app.api.products import Workflow
from app.state import ProductRecord
from app.tools.shopify import NotBuyableError, ShopifyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["orders"])

TEST_ORDER_FAILED = "Shopify could not record the test order right now. Please try again."

_REASONS = {
    "not_found": "the product is no longer in the shop",
    "not_active": "the product is not active yet; approve and publish it first",
    "not_published": "the product is not published to the online store",
    "no_variant": "the product has no variant to order",
    "sold_out": "the product is sold out",
}

Recorder = Callable[[ProductRecord], SimulatedSale]


def get_recorder() -> Recorder:
    """The test-order recorder; tests override this dependency with a stub."""
    return record_test_sale


@router.post("/products/{productId}/test-order")
def simulate_sale(
    productId: str,
    workflow: Workflow,
    record_sale: Annotated[Recorder, Depends(get_recorder)],
) -> SimulatedSale:
    """Simulate a purchase by recording a Shopify test order for one unit; nobody is charged."""
    state = workflow.snapshot(productId)
    product = state.get("product") if state else None
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No product {productId!r} in this service")
    record = ProductRecord.model_validate(product)
    try:
        return record_sale(record)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except NotBuyableError as error:
        detail = f"No test order recorded: {_REASONS[error.reason]}"
        raise HTTPException(status.HTTP_409_CONFLICT, detail) from error
    except ShopifyError as error:
        logger.warning("test order failed for %s: %s", productId, error)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, TEST_ORDER_FAILED) from error
