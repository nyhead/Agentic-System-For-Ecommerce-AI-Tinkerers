"""Customer chat: open a support conversation, load it and post a message to the support agent."""

import logging
import os
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.agents.support import (
    ConversationAccessError,
    DemoChatChannel,
    DemoOrders,
    PublicConversation,
    PublicReply,
    SupportDesk,
    SupportError,
    UnknownConversationError,
    demo_watch_catalog,
)
from app.tools.llm import LLMClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/support", tags=["support"])

# One message for an unknown conversation and for another customer's: the caller cannot tell.
NOT_FOUND = "This conversation is not available. Start a new chat."
SUPPORT_UNAVAILABLE = "The shop could not take this message right now. Please try again."
LLM_SETTINGS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

_desk: SupportDesk | None = None
_desk_lock = threading.Lock()


class OpenRequest(BaseModel):
    """No identity fields: the chat is anonymous, so every caller is an unverified customer."""

    customerName: str = ""
    productId: str | None = None


class MessageRequest(BaseModel):
    # The agent enforces the length, emptiness and id rules; a ValueError from it becomes a 400.
    text: str
    clientMessageId: str


def _model() -> LLMClient | None:
    configured = all(os.environ.get(name, "").strip() for name in LLM_SETTINGS)
    return LLMClient() if configured else None


def get_desk() -> SupportDesk:
    """The process-wide support desk, built on first use; tests override this dependency."""
    global _desk
    with _desk_lock:
        if _desk is None:
            _desk = SupportDesk(
                catalog=demo_watch_catalog(),
                channel=DemoChatChannel(),
                orders=DemoOrders({}),
                llm=_model(),
            )
        return _desk


Desk = Annotated[SupportDesk, Depends(get_desk)]


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def open_conversation(body: OpenRequest, desk: Desk) -> PublicConversation:
    """Start an anonymous chat, optionally about the product page it was opened from."""
    try:
        conversation = desk.open_conversation(body.customerName, product_id=body.productId)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return desk.public_conversation(conversation.conversationId)


@router.get("/conversations/{conversationId}")
def load_conversation(conversationId: str, desk: Desk) -> PublicConversation:
    """The customer-safe view of one chat; the conversation id is its access token."""
    try:
        return desk.public_conversation(conversationId)
    except (UnknownConversationError, ConversationAccessError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from error


@router.post("/conversations/{conversationId}/messages")
def post_message(conversationId: str, body: MessageRequest, desk: Desk) -> PublicReply:
    """Answer one customer message; a retry with the same clientMessageId returns the same reply."""
    try:
        result = desk.receive(conversationId, body.text, client_message_id=body.clientMessageId)
        return desk.public_reply(result)
    except (UnknownConversationError, ConversationAccessError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except SupportError as error:
        logger.warning("support agent failed for %s: %s", conversationId, error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, SUPPORT_UNAVAILABLE) from error
