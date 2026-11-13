"""Listing workflow: draft, launch copy and analytics, then publish only on merchant approval."""

import logging
import operator
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from app.agents import analytics, marketing, product
from app.state import (
    AnalyticsResult,
    MarketingResult,
    MetricsRecord,
    ProductRecord,
    SocialAd,
    WorkflowState,
)
from app.tools.llm import Image, LLMClient, LLMError
from app.tools.shopify import ShopifyClient, ShopifyError

logger = logging.getLogger(__name__)

Agent = Literal["product", "marketing", "analytics", "merchant", "workflow"]

# The checkpointer only restores these classes from a checkpoint, and no other.
STATE_TYPES = (ProductRecord, MarketingResult, SocialAd, MetricsRecord, AnalyticsResult)

DRAFT_MODEL_FAILED = (
    "The product agent could not write the listing because the AI model gave no usable answer. "
    "Nothing was created in your store. Please try again in a minute."
)
DRAFT_INPUT_UNUSABLE = (
    "The product agent could not use this upload. Add a JPEG or PNG photo, a few notes about "
    "the product, or both, and try again."
)
DRAFT_SHOPIFY_FAILED = (
    "The listing was written, but it could not be saved as a draft in your Shopify store. "
    "Nothing is visible to customers. Check the store connection and try again."
)
MARKETING_FAILED = (
    "Your draft is saved in Shopify, hidden from customers, but the marketing agent could not "
    "write the launch copy. Please try again in a minute."
)
ANALYTICS_FAILED = (
    "Your draft and launch copy are ready, but the analytics agent could not read the shop "
    "numbers. Try again, or continue without them."
)
PUBLISH_FAILED = (
    "Your approval was received, but the listing could not be published in Shopify. It is "
    "still a hidden draft. Check the store connection and try again."
)


class WorkflowEvent(TypedDict):
    """One entry of the action log: who acted, what they did in plain words, and when."""

    agent: Agent
    action: str
    ok: bool
    at: str  # ISO 8601, UTC


class ProductEvent(WorkflowEvent):
    """An action-log entry tagged with its product, for the log across all workflows."""

    productId: str


class RunState(WorkflowState, total=False):
    """WorkflowState plus the run's inputs and its action log."""

    productId: str
    notes: str
    photo: bytes  # kept in the checkpoint only; never returned to callers
    photoMimeType: str
    sourceImageUrl: str
    events: Annotated[list[WorkflowEvent], operator.add]


class WorkflowError(Exception):
    """A request the workflow cannot carry out; nothing was written to Shopify."""


class UnknownWorkflowError(WorkflowError):
    pass


class NotAwaitingApprovalError(WorkflowError):
    pass


class WorkflowExistsError(WorkflowError):
    """`start` for a product whose workflow is waiting or finished; new inputs were not used."""


def _event(agent: Agent, action: str, ok: bool = True) -> WorkflowEvent:
    return {
        "agent": agent,
        "action": action,
        "ok": ok,
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }


def _failed(agent: Agent, action: str, message: str, error: Exception) -> RunState:
    # The technical cause goes to the server log; the merchant sees only `message`.
    expected = isinstance(error, (LLMError, ShopifyError, ValueError))
    logger.warning(
        "%s agent failed: %s: %s", agent, type(error).__name__, error, exc_info=not expected
    )
    return {"error": message, "events": [_event(agent, action, ok=False)]}


def _public(values: dict[str, Any]) -> RunState:
    # The checkpoint of this graph holds a RunState; the photo bytes stay in it.
    return cast(RunState, {key: value for key, value in values.items() if key != "photo"})


def default_checkpointer() -> InMemorySaver:
    """In-memory checkpoints: every workflow is lost when the process restarts."""
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES))


class Workflow:
    """The listing workflow with its model client, Shopify client and checkpointer injected.

    A client left as None is built by the agent at call time from the environment.
    """

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        shopify: ShopifyClient | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._llm = llm
        self._shopify = shopify
        self._checkpointer = checkpointer if checkpointer is not None else default_checkpointer()
        self._graph = self._build()
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._started: list[str] = []

    @property
    def graph(self) -> CompiledStateGraph[RunState, None, RunState, RunState]:
        return self._graph

    def start(
        self,
        product_id: str,
        notes: str,
        *,
        photo: bytes | None = None,
        photo_mime_type: str = "image/jpeg",
        image_url: str | None = None,
        metrics: MetricsRecord | None = None,
    ) -> RunState:
        """Run draft, marketing and analytics, then stop with approval "pending".

        Raises WorkflowExistsError for a workflow that is waiting or finished. One that stopped
        with an error, or was interrupted mid-step, is run again, keeping what already succeeded.
        """
        if not product_id.strip():
            raise ValueError("product_id is empty")
        if photo is not None and image_url is not None:
            raise ValueError("pass photo or image_url, not both")
        if metrics is not None and metrics.productId != product_id:
            raise ValueError(f"metrics are for {metrics.productId!r}, not {product_id!r}")
        with self._lock(product_id):
            config = _config(product_id)
            current = self._graph.get_state(config)
            previous = current.values
            paused = current.next == ("decision",)
            failed = "error" in previous or (bool(current.next) and not paused)
            if previous and not failed:
                approval = previous.get("approval", "unknown")
                raise WorkflowExistsError(
                    f"A workflow for product {product_id!r} already exists (approval: {approval})"
                )
            seed: RunState = {"productId": product_id, "notes": notes}
            if photo is not None:
                seed["photo"] = photo
                seed["photoMimeType"] = photo_mime_type
            if image_url is not None:
                seed["sourceImageUrl"] = image_url
            if metrics is not None:
                seed["metrics"] = metrics
            if previous:
                # Retry: start a clean run that keeps what already succeeded, so no second
                # model draft can disagree with the product that is already in Shopify.
                kept = cast(RunState, previous)
                if "product" in kept:
                    seed["product"] = kept["product"]
                if "marketing" in kept:
                    seed["marketing"] = kept["marketing"]
                if metrics is None and "metrics" in kept:
                    seed["metrics"] = kept["metrics"]
                    if "analytics" in kept:
                        seed["analytics"] = kept["analytics"]
                if photo is None and image_url is None:
                    if "photo" in kept:
                        seed["photo"] = kept["photo"]
                        seed["photoMimeType"] = kept.get("photoMimeType", "image/jpeg")
                    if "sourceImageUrl" in kept:
                        seed["sourceImageUrl"] = kept["sourceImageUrl"]
                seed["events"] = kept.get("events", [])
                self._checkpointer.delete_thread(product_id)
            with self._guard:
                if product_id not in self._started:
                    self._started.append(product_id)
            self._graph.invoke(seed, config)
            return _public(self._graph.get_state(config).values)

    def decide(self, product_id: str, approved: bool) -> RunState:
        """Record the merchant's decision; publish only when `approved` is True."""
        if not isinstance(approved, bool):
            raise TypeError("approved must be True or False")
        with self._lock(product_id):
            config = _config(product_id)
            current = self._graph.get_state(config)
            if not current.values:
                raise UnknownWorkflowError(f"No workflow for product {product_id!r}")
            if "decision" not in current.next:
                raise NotAwaitingApprovalError(
                    f"Product {product_id!r} is not waiting for approval"
                )
            self._graph.invoke(Command(resume=approved), config)
            return _public(self._graph.get_state(config).values)

    def snapshot(self, product_id: str) -> RunState | None:
        values = self._graph.get_state(_config(product_id)).values
        return _public(values) if values else None

    def products(self) -> list[str]:
        """Ids of the workflows this process has started, in start order."""
        with self._guard:
            return list(self._started)

    def events(self) -> list[ProductEvent]:
        """Every workflow's action log, merged and sorted by time."""
        merged: list[ProductEvent] = []
        for product_id in self.products():
            state = self.snapshot(product_id) or {}
            for event in state.get("events", []):
                merged.append({**event, "productId": product_id})
        return sorted(merged, key=lambda event: event["at"])

    def _lock(self, product_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(product_id, threading.Lock())

    def _build(self) -> CompiledStateGraph[RunState, None, RunState, RunState]:
        builder = StateGraph(RunState)
        builder.add_node("draft", self._draft)
        builder.add_node("marketing", self._marketing)
        builder.add_node("analytics", self._analytics)
        builder.add_node("review", self._review)
        builder.add_node("decision", self._decision)
        builder.add_node("publish", self._publish)
        builder.add_edge(START, "draft")
        builder.add_conditional_edges("draft", _stop_on_error("marketing"), ["marketing", END])
        builder.add_conditional_edges("marketing", _after_marketing, ["analytics", "review", END])
        builder.add_conditional_edges("analytics", _stop_on_error("review"), ["review", END])
        builder.add_edge("review", "decision")
        # "decision" routes itself with Command: to "publish" only on an explicit approval.
        builder.add_edge("publish", END)
        return builder.compile(checkpointer=self._checkpointer)

    def _draft(self, state: RunState) -> RunState:
        record = state.get("product")
        events: list[WorkflowEvent] = []
        if record is None:
            try:
                record = product.draft_listing(
                    state["productId"], state.get("notes", ""), image=_image(state), llm=self._llm
                )
            except LLMError as error:
                return _failed("product", "Could not write the listing", DRAFT_MODEL_FAILED, error)
            except ValueError as error:
                return _failed(
                    "product", "Could not use the photo or notes", DRAFT_INPUT_UNUSABLE, error
                )
            except Exception as error:
                return _failed("product", "Could not write the listing", DRAFT_MODEL_FAILED, error)
            events.append(_event("product", "Wrote a draft title and description"))
        if record.shopifyProductId is None:
            try:
                record = product.create_draft(
                    record,
                    image_url=state.get("sourceImageUrl"),
                    photo=state.get("photo"),
                    photo_mime_type=state.get("photoMimeType", "image/jpeg"),
                    client=self._shopify,
                )
            except Exception as error:
                failure = _failed(
                    "product", "Could not save the draft in Shopify", DRAFT_SHOPIFY_FAILED, error
                )
                return {**failure, "product": record, "events": events + failure["events"]}
            events.append(_event("product", "Created the draft in Shopify, hidden from customers"))
        return {"product": record, "events": events}

    def _marketing(self, state: RunState) -> RunState:
        if "marketing" in state:
            return {}
        try:
            copy = marketing.write_marketing(state["product"], llm=self._llm)
        except Exception as error:
            return _failed("marketing", "Could not write the launch copy", MARKETING_FAILED, error)
        action = "Wrote an SEO title, a meta description and two social ads"
        return {"marketing": copy, "events": [_event("marketing", action)]}

    def _analytics(self, state: RunState) -> RunState:
        if "analytics" in state:
            return {}
        metrics = state["metrics"]
        try:
            result = analytics.analyze(metrics)
        except Exception as error:
            return _failed("analytics", "Could not read the metrics", ANALYTICS_FAILED, error)
        action = f"Recommended a next step from {metrics.source} metrics"
        return {"analytics": result, "events": [_event("analytics", action)]}

    def _review(self, state: RunState) -> RunState:
        events: list[WorkflowEvent] = []
        if "metrics" not in state:
            events.append(_event("analytics", "Skipped: no shop numbers for this product yet"))
        events.append(_event("workflow", "Waiting for the shop owner to approve or reject"))
        return {"approval": "pending", "events": events}

    def _decision(self, state: RunState) -> Command[Literal["publish", "__end__"]]:
        # Pauses the run; `decide` resumes it with the merchant's answer.
        answer = interrupt({"productId": state["productId"], "question": "Publish this listing?"})
        if answer is True:
            return Command(
                goto="publish", update={"events": [_event("merchant", "Approved the listing")]}
            )
        rejected = _event("merchant", "Rejected the listing; nothing was published")
        # "__end__" is END spelled as the literal that the return annotation allows.
        return Command(goto="__end__", update={"approval": "rejected", "events": [rejected]})

    def _publish(self, state: RunState) -> RunState:
        try:
            record = product.publish(state["product"], client=self._shopify)
        except Exception as error:
            return _failed("product", "Could not publish the listing", PUBLISH_FAILED, error)
        published = _event("product", "Published the product to the Online Store")
        return {"product": record, "approval": "approved", "events": [published]}


def _config(product_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": product_id}}


def _image(state: RunState) -> Image | None:
    if "photo" in state:
        return Image.from_bytes(state["photo"], state.get("photoMimeType", "image/jpeg"))
    if "sourceImageUrl" in state:
        return Image.from_url(state["sourceImageUrl"])
    return None


def _stop_on_error(next_node: str) -> Callable[[RunState], str]:
    def route(state: RunState) -> str:
        return END if "error" in state else next_node

    return route


def _after_marketing(state: RunState) -> str:
    if "error" in state:
        return END
    return "analytics" if "metrics" in state else "review"


_default: Workflow | None = None
_default_guard = threading.Lock()


def default_workflow() -> Workflow:
    """The process-wide workflow, built on first use so that importing needs no credentials."""
    global _default
    with _default_guard:
        if _default is None:
            _default = Workflow()
        return _default


def start(
    product_id: str,
    notes: str,
    *,
    photo: bytes | None = None,
    photo_mime_type: str = "image/jpeg",
    image_url: str | None = None,
    metrics: MetricsRecord | None = None,
) -> RunState:
    return default_workflow().start(
        product_id,
        notes,
        photo=photo,
        photo_mime_type=photo_mime_type,
        image_url=image_url,
        metrics=metrics,
    )


def decide(product_id: str, approved: bool) -> RunState:
    return default_workflow().decide(product_id, approved)


def snapshot(product_id: str) -> RunState | None:
    return default_workflow().snapshot(product_id)


def products() -> list[str]:
    return default_workflow().products()


def events() -> list[ProductEvent]:
    return default_workflow().events()
