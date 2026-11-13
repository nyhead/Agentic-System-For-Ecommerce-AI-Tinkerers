"""Shopper chat: the sales agent answers a question about one product and links to checkout."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agents.sales import MAX_QUESTION_CHARS, SalesReply, answer_shopper
from app.api.products import Workflow
from app.state import ProductRecord
from app.tools.llm import LLMError
from app.tools.shopify import ShopifyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

SALES_UNAVAILABLE = "The sales agent could not answer right now. Please try again in a minute."

Answerer = Callable[[ProductRecord, str], SalesReply]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def get_answerer() -> Answerer:
    """The sales agent; tests override this dependency with a stub."""
    return answer_shopper


@router.post("/products/{productId}/chat")
def chat(
    productId: str,
    body: ChatRequest,
    workflow: Workflow,
    answer: Annotated[Answerer, Depends(get_answerer)],
) -> SalesReply:
    """Answer a shopper from the product's facts, with a checkout link only when it can be bought.

    Works for a product this process drafted; before approval the answer says it is not on sale.
    """
    state = workflow.snapshot(productId)
    product = state.get("product") if state else None
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No product {productId!r} in this service")
    record = ProductRecord.model_validate(product)
    try:
        return answer(record, body.question)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except (ShopifyError, LLMError) as error:
        logger.warning("sales agent failed for %s: %s", productId, error)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, SALES_UNAVAILABLE) from error
