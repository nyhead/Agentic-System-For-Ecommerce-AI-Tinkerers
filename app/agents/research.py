"""Market research and pricing agent: comparable listings via Apify, a price proposal, copy edits.

Pipeline: identify the product -> build one focused query -> collect listings through Apify
Actors (bounded, platform spending limits) -> normalize, deduplicate, classify -> statistics in
code (app/agents/pricing.py) -> proposals for the merchant to review. Nothing here writes to
Shopify; approving a proposal only records the decision.

Competitor listings are untrusted data. Their titles are matched and counted in code; no
competitor text is sent to the model, so a listing cannot inject instructions into it.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents import pricing
from app.state import (
    ChangeDecision,
    Comparable,
    ExcludedCandidate,
    ObservedTerm,
    PositioningHypothesis,
    PricingRecommendation,
    ProductFact,
    ProposedChange,
    ResearchReport,
    ResearchRequest,
    ResearchStatus,
    ResearchUsage,
    SourceRun,
)
from app.tools.apify import ApifyClient, ApifyError, RunLimits
from app.tools.llm import LLMClient, LLMError
from app.tools.marketplaces import MARKETS, SOURCES, SearchSpec

logger = logging.getLogger(__name__)

RUNNING: frozenset[ResearchStatus] = frozenset(
    {
        "queued",
        "finding_comparables",
        "checking_matches",
        "analyzing_prices",
        "preparing_improvements",
    }
)

# Attributes buyers of a category expect, and which of them move the price most.
_CATEGORY_ATTRIBUTES: dict[str, tuple[list[str], list[str]]] = {
    "watch": (
        [
            "Reference number",
            "Case diameter",
            "Movement calibre",
            "Working condition",
            "Service history",
            "Box and papers",
            "Year of manufacture",
        ],
        ["Reference number", "Working condition"],
    ),
    "shoe": (["Size", "Condition", "Original box", "Colorway"], ["Size", "Condition"]),
    "camera": (["Shutter count", "Mount", "Condition", "Included accessories"], ["Condition"]),
    "phone": (["Storage", "Carrier lock", "Battery health", "Condition"], ["Storage", "Condition"]),
}
_DEFAULT_ATTRIBUTES = (["Condition", "Included accessories", "Dimensions"], ["Condition"])

_EXCLUDE_PHRASES = (
    "for parts",
    "not working",
    "spares",
    "repair",
    "broken",
    "damaged",
    "as is",
    "replica",
    "fake",
    "homage",
    "empty box",
    "box only",
    "manual only",
    "parts only",
    "lot of",
)
_ACCESSORY_PHRASES = (
    "strap only",
    "band only",
    "bracelet only",
    "dial only",
    "crown only",
    "crystal only",
    "movement only",
    "case only",
    "buckle only",
    "strap for",
    "band for",
    "buckle for",
    "dial for",
    "crown for",
    "crystal for",
    "hands for",
    "movement for",
    "case back for",
    "watch box",
    "box for",
    "case for",
    "cover for",
    "charger for",
)
# Values in one group are mutually exclusive: a quartz watch is not a match for an automatic.
_CONFLICT_GROUPS = (
    ("automatic", "quartz", "manual wind", "hand wind", "solar"),
    ("18k", "14k", "9k", "gold plated", "stainless steel", "titanium"),
    ("ladies", "women", "mens"),
)
_STOPWORDS = frozenset(
    "a an and the for with of in on to by from at or new used pre owned preowned pre-owned "
    "watch watches item items free shipping ship fast see photos look rare nice great good "
    "excellent condition box only s".split()
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _fact(facts: Sequence[ProductFact], *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for fact in facts:
        if fact.name.strip().lower() in wanted:
            return fact.value.strip()
    return None


def category_key(category: str | None) -> str:
    text = (category or "").lower()
    for key, words in (
        ("watch", ("watch",)),
        ("shoe", ("shoe", "sneaker", "boot")),
        ("camera", ("camera", "lens")),
        ("phone", ("phone",)),
    ):
        if any(word in text for word in words):
            return key
    return "default"


@dataclass(frozen=True)
class Identity:
    brand: str | None
    model: str | None
    reference: str | None
    confirmed: bool  # False when brand or model came only from the photos
    query: str | None
    critical_missing: list[str]


def identify(request: ResearchRequest) -> Identity:
    confirmed, suggested = request.confirmedFacts, request.imageSuggestions
    brand = _fact(confirmed, "brand")
    model = _fact(confirmed, "model", "model line")
    identity_confirmed = bool(brand and model)
    brand = brand or _fact(suggested, "brand")
    model = model or _fact(suggested, "model", "model line")
    reference = _fact(confirmed, "reference", "reference number", "model number", "mpn")
    condition = _fact(confirmed, "condition")
    parts = [p for p in (brand, model, reference) if p]
    query = " ".join(parts) if parts else (request.title.strip() or None)
    _, critical = _CATEGORY_ATTRIBUTES.get(category_key(request.category), _DEFAULT_ATTRIBUTES)
    known = {f.name.strip().lower() for f in confirmed}
    if reference:
        known |= {"reference number"}
    if condition:
        known |= {"condition", "working condition"}
    missing = [name for name in critical if name.lower() not in known]
    return Identity(brand, model, reference, identity_confirmed, query, missing)


def _contains(title_norm: str, phrase: str) -> bool:
    words = _norm(phrase)
    if not words:
        return True
    compact_title = title_norm.replace(" ", "")
    return f" {words} " in f" {title_norm} " or words.replace(" ", "") in compact_title


def _facts_text(request: ResearchRequest) -> str:
    facts = [*request.confirmedFacts, *request.imageSuggestions]
    return _norm(" ".join([request.title, *(f"{f.name} {f.value}" for f in facts)]))


def classify(
    comps: Sequence[Comparable], identity: Identity, request: ResearchRequest
) -> tuple[list[Comparable], list[ExcludedCandidate]]:
    """Dedupe, drop unusable or unrelated rows, and label the rest same_model or related."""
    ours = _facts_text(request)
    kept: list[Comparable] = []
    excluded: list[ExcludedCandidate] = []
    seen_ids: dict[str, str] = {}
    seen_keys: dict[tuple[str, float | None, str | None], str] = {}

    def drop(comp: Comparable, reason: str) -> None:
        excluded.append(
            ExcludedCandidate(
                ref=comp.ref, sourceUrl=comp.sourceUrl, title=comp.title, reason=reason
            )
        )

    for comp in comps:
        title = _norm(comp.title)
        listing_key = comp.listingId or ""
        price_key = (
            title,
            comp.price.amount if comp.price else None,
            comp.price.currency if comp.price else None,
        )
        if listing_key and listing_key in seen_ids:
            drop(comp, f"Duplicate of {seen_ids[listing_key]} (same eBay item number)")
            continue
        if price_key in seen_keys:
            drop(comp, f"Duplicate of {seen_keys[price_key]} (same title and price)")
            continue
        if listing_key:
            seen_ids[listing_key] = comp.ref
        seen_keys[price_key] = comp.ref
        condition = _norm(comp.condition or "")
        if any(_contains(title, p) for p in _EXCLUDE_PHRASES) or "for parts" in condition:
            drop(comp, "Incomplete, damaged, for parts or not genuine, per its title or condition")
            continue
        if any(_contains(title, p) for p in _ACCESSORY_PHRASES):
            drop(comp, "Accessory or part, not the whole product")
            continue
        if comp.priceType == "auction_bid":
            drop(comp, "Live auction bid: not an asking or a sale price")
            continue
        if comp.price is None:
            drop(comp, "No price with a known currency")
            continue
        if comp.priceType == "unknown":
            drop(comp, "Final price not disclosed (best offer accepted, or a variation range)")
            continue
        if identity.brand and not _contains(title, identity.brand):
            drop(comp, f"Unrelated: title does not name {identity.brand}")
            continue
        differences = []
        for group in _CONFLICT_GROUPS:
            ours_has = [v for v in group if _contains(ours, v)]
            theirs = [v for v in group if _contains(title, v) and v not in ours_has]
            if ours_has and theirs:
                differences.append(f"Listed as {theirs[0]}; ours is {ours_has[0]}")
        model_ok = identity.model is None or _contains(title, identity.model)
        ref_ok = identity.reference is None or _contains(title, identity.reference)
        match = "same_model" if model_ok and ref_ok and not differences else "related"
        reasons = [f"names {identity.brand}" if identity.brand else "matches the query"]
        if identity.model:
            reasons.append(f"{'names' if model_ok else 'does not name'} {identity.model}")
        if identity.reference:
            reasons.append(f"reference {identity.reference} {'stated' if ref_ok else 'absent'}")
        if not identity.reference:
            differences.append("Reference number not compared: ours is unknown")
        kept.append(
            comp.model_copy(
                update={
                    "brand": identity.brand if identity.brand else None,
                    "model": identity.model if model_ok else None,
                    "reference": identity.reference if identity.reference and ref_ok else None,
                    "match": match,
                    "matchRationale": "Title " + ", ".join(reasons) + ".",
                    "differences": differences,
                }
            )
        )
    return kept, excluded


def observed_terms(comps: Sequence[Comparable], identity: Identity) -> list[ObservedTerm]:
    """Words that recur in same-model titles, counted once per listing."""
    own = set(_norm(" ".join(p for p in (identity.brand, identity.model) if p)).split())
    counts: Counter[str] = Counter()
    refs: dict[str, list[str]] = {}
    same = [c for c in comps if c.match == "same_model"]
    for comp in same:
        for word in set(_norm(comp.title).split()):
            if word in _STOPWORDS or word in own or len(word) < 3 or word.isdigit():
                continue
            counts[word] += 1
            refs.setdefault(word, []).append(comp.ref)
    floor = max(2, len(same) // 5)
    return [
        ObservedTerm(term=word, count=n, refs=refs[word])
        for word, n in counts.most_common(12)
        if n >= floor
    ]


class ListingCopy(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default=[], max_length=12)


COPY_SYSTEM = (
    "You write e-commerce listing copy for a small shop. Use only the facts given. Facts marked "
    "TO CONFIRM must appear inside square brackets, e.g. [Confirm: automatic movement]. Never "
    "add specifications, dates, authenticity, condition, service history or measurements that "
    "are not listed. Plain, warm, factual English."
)


def _write_copy(
    request: ResearchRequest, identity: Identity, missing: Sequence[str], llm: LLMClient | None
) -> tuple[ListingCopy, bool]:
    """(copy, written_by_model). Falls back to a deterministic template if the model fails."""
    confirmed = [f"{f.name}: {f.value}" for f in request.confirmedFacts]
    suggested = [f"{f.name}: {f.value} (TO CONFIRM)" for f in request.imageSuggestions]
    title_parts = [p for p in (identity.brand, identity.model, identity.reference) if p]
    extras: list[str] = []
    for name in ("movement", "material", "color", "colour", "size", "strap"):
        value = _fact(request.confirmedFacts, name) or _fact(request.imageSuggestions, name)
        if not value:
            continue
        value = value.split(",")[0].strip()  # "Automatic, per dial text" -> "Automatic"
        if name == "strap" and "strap" not in value.lower():
            value = f"{value} strap"
        if len(" ".join(title_parts + extras + [value])) <= 80:
            extras.append(value)
    if extras:
        title_parts = [" ".join(title_parts + extras[:1])] + extras[1:]
    template_title = ", ".join(title_parts) or request.title or "Product"
    lines = [f"{template_title}."]
    if confirmed:
        lines.append("Details: " + "; ".join(confirmed) + ".")
    if suggested:
        lines.append(
            "Read from the photos, to confirm: "
            + "; ".join(f"[Confirm: {f.name} {f.value}]" for f in request.imageSuggestions)
            + "."
        )
    if missing:
        lines.append("Still to add: " + ", ".join(f"[{name}]" for name in missing) + ".")
    fallback = ListingCopy(
        title=template_title[:120],
        description=" ".join(lines)[:2000],
        tags=[t for t in (identity.brand, identity.model, request.category) if t][:12],
    )
    prompt = (
        f"Category: {request.category or 'not stated'}\n"
        f"Confirmed facts:\n- " + ("\n- ".join(confirmed) or "none") + "\n"
        "Facts read from photos (TO CONFIRM):\n- " + ("\n- ".join(suggested) or "none") + "\n"
        f"Details buyers expect that we do not have yet: {', '.join(missing) or 'none'}\n"
        "Write a clear product title (max 80 characters), a description of 60 to 120 words that "
        "ends with a short list of the missing details in square brackets, and up to 8 tags."
    )
    try:
        client = llm or LLMClient()
        result = client.structured(prompt, ListingCopy, system=COPY_SYSTEM, temperature=0.2)
        return result, True
    except (LLMError, ValueError) as error:
        logger.info("research copy fell back to the template: %s", error)
        return fallback, False


@dataclass
class ResearchConfig:
    sources: tuple[str, ...] = ("ebay-sold", "ebay-active")
    max_results_per_source: int = 10
    max_charge_usd_per_run: float = 0.20
    run_timeout_secs: int = 180
    memory_mbytes: int = 1024
    sold_days: int = 90
    cache_ttl_secs: int = 24 * 3600
    poll_wait_secs: int = 30
    state_path: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "ResearchConfig":
        def number(name: str, default: float, low: float, high: float) -> float:
            try:
                return min(max(float(env.get(name, default)), low), high)
            except ValueError:
                return default

        sources = tuple(
            s.strip()
            for s in env.get("RESEARCH_SOURCES", "ebay-sold,ebay-active").split(",")
            if s.strip() in SOURCES
        )
        return cls(
            sources=sources or ("ebay-sold",),
            max_results_per_source=int(number("RESEARCH_MAX_RESULTS_PER_SOURCE", 10, 1, 50)),
            max_charge_usd_per_run=number("RESEARCH_MAX_CHARGE_USD_PER_RUN", 0.20, 0.01, 5.0),
            run_timeout_secs=int(number("RESEARCH_RUN_TIMEOUT_SECS", 180, 30, 900)),
            memory_mbytes=int(number("RESEARCH_RUN_MEMORY_MB", 1024, 256, 4096)),
            sold_days=int(number("RESEARCH_SOLD_DAYS", 90, 1, 90)),
            cache_ttl_secs=int(number("RESEARCH_CACHE_TTL_HOURS", 24, 0, 24 * 30) * 3600),
            state_path=env.get("RESEARCH_STATE_PATH") or None,
        )

    def fingerprint(self) -> dict[str, Any]:
        return {
            "sources": list(self.sources),
            "max": self.max_results_per_source,
            "soldDays": self.sold_days,
        }


def draft_version(request: ResearchRequest) -> str:
    price = request.currentPrice.model_dump() if request.currentPrice else None
    payload = json.dumps([request.title, request.description, price], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def cache_key(request: ResearchRequest, config: ResearchConfig) -> str:
    payload = json.dumps([request.model_dump(), config.fingerprint()], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class ResearchError(Exception):
    pass


class UnknownResearchError(ResearchError):
    pass


class StaleDraftError(ResearchError):
    """The product changed after research; the merchant must review the proposal again."""


@dataclass
class _Job:
    report: ResearchReport
    request: ResearchRequest
    key: str
    created: float = field(default_factory=time.time)


class ResearchService:
    """In-process research jobs, keyed by product facts, market and configuration.

    A finished job is reused until `cache_ttl_secs` passes or the caller asks to refresh. Each
    source's Apify run ID is saved before the service waits on it, so a reconnect or a restarted
    worker polls the same run instead of paying for a new one. With RESEARCH_STATE_PATH set the
    jobs also survive a process restart.
    """

    def __init__(
        self,
        *,
        apify: ApifyClient | None = None,
        llm: LLMClient | None = None,
        config: ResearchConfig | None = None,
        run_in_background: bool = True,
        fixture_items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._apify = apify or ApifyClient()
        self._llm = llm
        self._config = config or ResearchConfig.from_env()
        self._background = run_in_background
        # Only tests pass fixtures; a report built from them says dataMode "fixture".
        self._fixtures = fixture_items
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._load()

    @property
    def config(self) -> ResearchConfig:
        return self._config

    # ---- public API -------------------------------------------------------------------------

    def start(self, request: ResearchRequest, *, refresh: bool = False) -> ResearchReport:
        key = cache_key(request, self._config)
        with self._lock:
            if not refresh:
                for job in sorted(self._jobs.values(), key=lambda j: j.created, reverse=True):
                    fresh = time.time() - job.created < self._config.cache_ttl_secs
                    if (
                        job.key == key
                        and job.report.status != "failed"
                        and (fresh or job.report.status in RUNNING)
                    ):
                        return job.report.model_copy(deep=True)
            job = self._new_job(request, key)
            self._jobs[job.report.researchId] = job
            self._save()
            if job.report.status != "queued":
                return job.report.model_copy(deep=True)
        self._launch(job.report.researchId)
        return self.get(job.report.researchId)

    def get(self, research_id: str) -> ResearchReport:
        with self._lock:
            job = self._jobs.get(research_id)
            if job is None:
                raise UnknownResearchError(f"No research {research_id!r}")
            return job.report.model_copy(deep=True)

    def latest(self, product_id: str) -> ResearchReport | None:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.report.productId == product_id]
            if not jobs:
                return None
            return max(jobs, key=lambda j: j.created).report.model_copy(deep=True)

    def decide(
        self,
        research_id: str,
        *,
        kind: Literal["content", "price"],
        change_ids: Sequence[str],
        approved: bool,
        current: Mapping[str, str | None],
    ) -> ResearchReport:
        """Record an approval or rejection of content or price changes, never both at once.

        `current` holds the product's field values now. If any changed since research, nothing
        is approved and the decision says the merchant must review again. A repeated identical
        decision returns the first one; nothing is recorded twice.
        """
        if kind not in ("content", "price"):
            raise ValueError("kind must be content or price")
        ids = sorted(set(change_ids))
        if not ids:
            raise ValueError("choose at least one change")
        with self._lock:
            job = self._jobs.get(research_id)
            if job is None:
                raise UnknownResearchError(f"No research {research_id!r}")
            report = job.report
            changes = {c.id: c for c in report.proposedChanges}
            unknown = [i for i in ids if i not in changes]
            if unknown:
                raise ValueError(f"unknown change ids: {', '.join(unknown)}")
            wrong = [i for i in ids if changes[i].kind != kind]
            if wrong:
                raise ValueError(
                    f"{', '.join(wrong)} are not {kind} changes; approve content and price "
                    "separately"
                )
            decision_id = hashlib.sha256(
                json.dumps([research_id, kind, ids, approved]).encode()
            ).hexdigest()[:16]
            for existing in report.decisions:
                if existing.decisionId == decision_id:
                    return report.model_copy(deep=True)
            stale = [
                changes[i].field
                for i in ids
                if changes[i].field in current
                and (current[changes[i].field] or "") != (changes[i].currentValue or "")
            ]
            outcome: Literal["recorded", "needs_new_review"]
            if stale and approved:
                outcome, note = (
                    "needs_new_review",
                    (
                        "Changed since research: "
                        + ", ".join(sorted(set(stale)))
                        + ". Nothing was approved; refresh the research or review again."
                    ),
                )
            else:
                outcome = "recorded"
                note = (
                    "Approved. Recorded for the product draft; nothing was written to Shopify."
                    if approved
                    else "Rejected; nothing will change."
                )
            report.decisions.append(
                ChangeDecision(
                    decisionId=decision_id,
                    kind=kind,
                    changeIds=ids,
                    approved=approved,
                    outcome=outcome,
                    note=note,
                    decidedAt=_now(),
                )
            )
            report.updatedAt = _now()
            self._save()
            if outcome == "needs_new_review":
                raise StaleDraftError(note)
            return report.model_copy(deep=True)

    def resume_pending(self) -> None:
        """Restart workers for jobs that were running when the process stopped."""
        with self._lock:
            pending = [rid for rid, j in self._jobs.items() if j.report.status in RUNNING]
        for research_id in pending:
            self._launch(research_id)

    # ---- job lifecycle ----------------------------------------------------------------------

    def _new_job(self, request: ResearchRequest, key: str) -> _Job:
        now = _now()
        config = self._config
        sources = [
            SourceRun(
                source=name,
                actorId=SOURCES[name].actor_id,
                estimatedMaxUsd=min(
                    SOURCES[name].estimated_max_usd(config.max_results_per_source),
                    config.max_charge_usd_per_run,
                ),
            )
            for name in config.sources
        ]
        identity = identify(request)
        report = ResearchReport(
            researchId=f"research-{uuid.uuid4().hex[:10]}",
            productId=request.productId,
            draftVersion=draft_version(request),
            status="queued",
            dataMode="fixture" if self._fixtures is not None else "live",
            country=request.country,
            currency=request.currency,
            goal=request.goal,
            queries=[identity.query] if identity.query else [],
            sources=sources,
            usage=ResearchUsage(
                estimatedMaxUsd=round(sum(s.estimatedMaxUsd for s in sources), 4),
                note="Estimate is the upper bound from per-result list prices and the per-run "
                "spending cap; reported cost comes from Apify after each run.",
            ),
            createdAt=now,
            updatedAt=now,
        )
        questions = []
        if request.country is None or request.currency is None:
            questions.append(
                "Which market do you sell in, and in which currency? Supported: "
                + ", ".join(f"{c} ({MARKETS[c][2]})" for c in MARKETS)
                + "."
            )
        elif request.country not in MARKETS:
            questions.append(
                f"Research does not cover {request.country} yet. Pick one of: "
                + ", ".join(MARKETS)
                + "."
            )
        if identity.query is None:
            questions.append("What is the product? Add a brand and model, or a title.")
        if questions:
            report.status = "needs_information"
            report.merchantQuestions = questions
            report.completedAt = now
            report.limitations = ["No paid collection ran: the market or product is unclear."]
        return _Job(report=report, request=request, key=key)

    def _launch(self, research_id: str) -> None:
        if self._background:
            threading.Thread(
                target=self._execute,
                args=(research_id,),
                name=f"research-{research_id}",
                daemon=True,
            ).start()
        else:
            self._execute(research_id)

    def _update(self, research_id: str, **changes: Any) -> None:
        with self._lock:
            report = self._jobs[research_id].report
            for name, value in changes.items():
                setattr(report, name, value)
            report.updatedAt = _now()
            self._save()

    def _execute(self, research_id: str) -> None:
        try:
            self._run_pipeline(research_id)
        except Exception as error:  # the job must end in a state the merchant can read
            logger.exception("research %s failed", research_id)
            self._update(
                research_id,
                status="failed",
                completedAt=_now(),
                error=f"Research stopped unexpectedly ({type(error).__name__}). "
                "No fixture or invented data was used. Try again.",
            )

    def _run_pipeline(self, research_id: str) -> None:
        with self._lock:
            job = self._jobs[research_id]
            request = job.request
        identity = identify(request)
        assert request.country is not None and request.currency is not None
        self._update(research_id, status="finding_comparables")
        spec = SearchSpec(
            query=identity.query or request.title,
            country=request.country,
            max_results=self._config.max_results_per_source,
            condition=_condition_filter(request),
            sold_days=self._config.sold_days,
        )
        with ThreadPoolExecutor(max_workers=max(1, len(self._config.sources))) as pool:
            batches = list(
                pool.map(lambda name: self._collect(research_id, name, spec), self._config.sources)
            )
        raw: list[Comparable] = []
        counter = 0
        for name, items in zip(self._config.sources, batches):
            for item in items:
                counter += 1
                comp = SOURCES[name].normalize(item, f"C{counter}")
                if comp is not None:
                    raw.append(comp)

        self._update(research_id, status="checking_matches")
        kept, excluded = classify(raw, identity, request)

        self._update(research_id, status="analyzing_prices", comparables=kept, excluded=excluded)
        stats = [
            *pricing.price_stats(kept, "sold", "same_model"),
            *pricing.price_stats(kept, "asking", "same_model"),
            *pricing.price_stats(kept, "sold", "related"),
            *pricing.price_stats(kept, "asking", "related"),
        ]
        recommendation = pricing.recommend(
            stats,
            currency=request.currency,
            goal=request.goal,
            identity_confirmed=identity.confirmed,
            missing_critical=identity.critical_missing,
            freshness=_freshness(kept, self._config.sold_days),
        )
        profitability = pricing.check_profitability(request.costs, recommendation)
        self._update(
            research_id,
            status="preparing_improvements",
            stats=stats,
            recommendation=recommendation,
            profitability=profitability,
        )

        improvements = self._improvements(request, identity, kept, recommendation)
        with self._lock:
            report = self._jobs[research_id].report
            failed = [s for s in report.sources if s.status != "SUCCEEDED"]
            reported = [s.reportedUsd for s in report.sources if s.reportedUsd is not None]
            assert report.usage is not None
            usage = report.usage.model_copy(
                update={"reportedUsd": round(sum(reported), 4) if reported else None}
            )
        limitations = list(improvements.pop("limitations"))
        limitations += [f"{s.source} ({s.actorId}): {s.error or s.status}" for s in failed]
        if not raw:
            status: ResearchStatus = "failed"
        elif failed:
            status = "partial_results"
        else:
            status = "ready_for_review"
        error = None
        if not raw:
            error = (
                "No comparable listings were collected, so nothing is proposed. "
                "No fixture or invented data was used."
            )
        self._update(
            research_id,
            status=status,
            usage=usage,
            limitations=limitations,
            error=error,
            completedAt=_now(),
            **improvements,
        )

    def _collect(self, research_id: str, name: str, spec: SearchSpec) -> list[dict[str, Any]]:
        source = SOURCES[name]
        if self._fixtures is not None:
            items = [dict(i) for i in self._fixtures.get(name, [])][: spec.max_results]
            self._set_source(
                research_id,
                name,
                status="SUCCEEDED",
                itemCount=len(items),
                startedAt=_now(),
                finishedAt=_now(),
            )
            return items
        run_id = self._source(research_id, name).apifyRunId
        try:
            if run_id is None:
                if not self._apify.configured:
                    self._set_source(
                        research_id,
                        name,
                        status="not_configured",
                        error="APIFY_TOKEN is not set on the server",
                    )
                    return []
                limits = RunLimits(
                    max_items=spec.max_results,
                    max_total_charge_usd=self._config.max_charge_usd_per_run,
                    timeout_secs=self._config.run_timeout_secs,
                    memory_mbytes=self._config.memory_mbytes,
                )
                run = self._apify.start_run(source.actor_id, source.build_input(spec), limits)
                run_id = run.id
                # Saved before waiting: a reconnect polls this run instead of starting another.
                self._set_source(
                    research_id, name, apifyRunId=run.id, status=run.status, startedAt=_now()
                )
            deadline = time.monotonic() + self._config.run_timeout_secs + 60
            run = self._apify.get_run(run_id)
            while not run.finished and time.monotonic() < deadline:
                run = self._apify.get_run(run_id, wait_secs=self._config.poll_wait_secs)
                self._set_source(
                    research_id, name, status=run.status, reportedUsd=run.usage_total_usd
                )
            if not run.finished:
                self._apify.abort_run(run_id)
                self._set_source(
                    research_id,
                    name,
                    status="ABORTED",
                    error="Stopped waiting after the configured timeout",
                )
            items = (
                self._apify.dataset_items(run.dataset_id, spec.max_results)
                if run.dataset_id
                else []
            )
            # TIMED-OUT or ABORTED (e.g. the spending cap) can still hold partial results.
            error = (
                None
                if run.status == "SUCCEEDED"
                else (run.status_message or f"Run ended with status {run.status}")
            )
            self._set_source(
                research_id,
                name,
                status=run.status,
                itemCount=len(items),
                reportedUsd=run.usage_total_usd,
                error=error,
                finishedAt=_now(),
            )
            return items
        except ApifyError as err:
            self._set_source(
                research_id, name, status="failed", error=str(err)[:300], finishedAt=_now()
            )
            return []

    def _source(self, research_id: str, name: str) -> SourceRun:
        with self._lock:
            for run in self._jobs[research_id].report.sources:
                if run.source == name:
                    return run.model_copy()
        raise KeyError(name)

    def _set_source(self, research_id: str, name: str, **changes: Any) -> None:
        with self._lock:
            report = self._jobs[research_id].report
            for run in report.sources:
                if run.source == name:
                    for key, value in changes.items():
                        setattr(run, key, value)
            report.updatedAt = _now()
            self._save()

    def _improvements(
        self,
        request: ResearchRequest,
        identity: Identity,
        kept: Sequence[Comparable],
        recommendation: PricingRecommendation,
    ) -> dict[str, Any]:
        expected, _ = _CATEGORY_ATTRIBUTES.get(category_key(request.category), _DEFAULT_ATTRIBUTES)
        known = {f.name.strip().lower() for f in request.confirmedFacts}
        spec_counts: Counter[str] = Counter()
        for comp in kept:
            for key in comp.specs:
                spec_counts[key] += 1
        seen_specs = [k for k, n in spec_counts.most_common(8) if n >= 2 and k.lower() not in known]
        missing = [a for a in expected if a.lower() not in known]
        missing += [a for a in request.missingAttributes if a not in missing]
        copy, by_model = _write_copy(request, identity, missing, self._llm)
        terms = observed_terms(kept, identity)
        provisional = bool(request.imageSuggestions) and not identity.confirmed
        same_refs = [c.ref for c in kept if c.match == "same_model"]

        changes = [
            ProposedChange(
                id="title",
                kind="content",
                field="title",
                currentValue=request.title or None,
                proposedValue=copy.title,
                rationale="Leads with brand and model, the words same-model listings use."
                + (" Brand and model are read from the photos." if provisional else ""),
                evidenceRefs=same_refs[:5],
                requiresConfirmation=provisional,
            ),
            ProposedChange(
                id="description",
                kind="content",
                field="description",
                currentValue=request.description or None,
                proposedValue=copy.description,
                rationale="Built only from your facts; bracketed items need your confirmation."
                + (" Written by the model." if by_model else " Written from a template."),
                requiresConfirmation=True,
            ),
        ]
        if copy.tags:
            changes.append(
                ProposedChange(
                    id="tags",
                    kind="content",
                    field="tags",
                    currentValue=None,
                    proposedValue=", ".join(copy.tags),
                    rationale="Tags from your product facts.",
                    requiresConfirmation=provisional,
                )
            )
        if recommendation.startingPrice is not None and recommendation.currency:
            current = request.currentPrice
            changes.append(
                ProposedChange(
                    id="price",
                    kind="price",
                    field="price",
                    currentValue=f"{current.amount:g} {current.currency}" if current else None,
                    proposedValue=f"{recommendation.startingPrice:g} {recommendation.currency}",
                    rationale=f"{recommendation.status.replace('_', ' ').capitalize()} starting "
                    f"price; supported range {recommendation.rangeLow:g}–"
                    f"{recommendation.rangeHigh:g} {recommendation.currency}. "
                    "No compare-at price is set from competitor prices.",
                    evidenceRefs=_basis_refs(kept, recommendation),
                    requiresConfirmation=recommendation.status != "recommended",
                )
            )

        questions = []
        if provisional:
            questions.append(
                "Please confirm the brand and model. Read from the photos: "
                + " ".join(p for p in (identity.brand, identity.model) if p)
                + "."
            )
        for name in identity.critical_missing:
            if recommendation.rangeLow is not None:
                questions.append(
                    f"What is the {name.lower()}? Same-model prices run from "
                    f"{recommendation.rangeLow:g} to {recommendation.rangeHigh:g} "
                    f"{recommendation.currency}; this detail decides where yours belongs."
                )
            else:
                questions.append(f"What is the {name.lower()}? It is needed to find exact matches.")
        for key in seen_specs:
            questions.append(f"Comparable listings state “{key}”. What is it for yours?")
        for name in missing:
            if name not in identity.critical_missing and len(questions) < 8:
                questions.append(f"Can you add the {name.lower()}?")

        positioning = []
        for term in terms[:2]:
            positioning.append(
                PositioningHypothesis(
                    audience=f"Buyers searching for “{term.term}”",
                    statement=f"{term.count} of {len(same_refs)} same-model titles use "
                    f"“{term.term}”. Use it only if it is true of your item.",
                    basis="observed",
                    evidenceRefs=term.refs[:5],
                )
            )
        positioning.append(
            PositioningHypothesis(
                audience={
                    "faster_sale": "Price-comparing buyers",
                    "balanced": "Buyers weighing price against condition",
                    "preserve_margin": "Buyers who value completeness and documentation",
                }[request.goal],
                statement="A hypothesis from your pricing goal, not measured demand. Clear photos "
                "and stated condition support it.",
                basis="hypothesis",
            )
        )

        specifications = list(request.confirmedFacts)
        specifications += [
            ProductFact(name=f.name, value=f"{f.value} (to confirm)")
            for f in request.imageSuggestions
            if f.name.lower() not in known
        ]
        limitations = [
            "Prices are compared as item prices without shipping; tax is not stated by eBay "
            "listings and stays unknown.",
            "No currency conversion: only listings priced in the target currency feed the "
            "recommendation; others are shown separately.",
            "Sold listings with an accepted best offer are excluded because eBay hides the "
            "final amount.",
            "Search-term frequencies count competitor titles; they are not search volumes.",
        ]
        return {
            "proposedChanges": changes,
            "merchantQuestions": questions[:8],
            "positioning": positioning,
            "observedTerms": terms,
            "specifications": specifications,
            "limitations": limitations,
            "assumptions": [
                f"Market {request.country}, currency {request.currency}, goal {request.goal}.",
                "Identity "
                + (
                    "confirmed by you."
                    if identity.confirmed
                    else "read from photos; treated as provisional."
                ),
            ],
        }

    # ---- persistence ------------------------------------------------------------------------

    def _save(self) -> None:
        path = self._config.state_path
        if not path:
            return
        payload = {
            rid: {
                "report": job.report.model_dump(),
                "request": job.request.model_dump(),
                "key": job.key,
                "created": job.created,
            }
            for rid, job in self._jobs.items()
        }
        target = Path(path)
        tmp = target.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload))
            tmp.replace(target)
        except OSError as error:
            logger.warning("could not save research state: %s", error)

    def _load(self) -> None:
        path = self._config.state_path
        if not path or not Path(path).exists():
            return
        try:
            payload = json.loads(Path(path).read_text())
            for rid, item in payload.items():
                self._jobs[rid] = _Job(
                    report=ResearchReport.model_validate(item["report"]),
                    request=ResearchRequest.model_validate(item["request"]),
                    key=item["key"],
                    created=float(item["created"]),
                )
        except (OSError, ValueError, KeyError) as error:
            logger.warning("could not load research state: %s", error)


def _condition_filter(request: ResearchRequest) -> str:
    condition = (_fact(request.confirmedFacts, "condition") or "").lower()
    if "new" in condition and "like new" not in condition:
        return "new"
    if condition:
        return "used"
    return "any"


def _freshness(comps: Sequence[Comparable], sold_days: int) -> str:
    sold = sorted(c.endedAt for c in comps if c.priceType == "sold" and c.endedAt)
    if sold:
        return f"Sales ended between {sold[0][:10]} and {sold[-1][:10]}"
    return f"Asking prices retrieved {_now()[:10]}; sold window {sold_days} days"


def _basis_refs(comps: Sequence[Comparable], recommendation: PricingRecommendation) -> list[str]:
    if not recommendation.basis:
        return []
    price_type, match, currency = recommendation.basis.split(":")
    return [
        c.ref
        for c in comps
        if c.priceType == price_type
        and c.match == match
        and c.price is not None
        and c.price.currency == currency
    ]


_default: ResearchService | None = None
_default_guard = threading.Lock()


def default_service() -> ResearchService:
    """The process-wide service, built on first use so importing needs no credentials."""
    global _default
    with _default_guard:
        if _default is None:
            _default = ResearchService()
            _default.resume_pending()
        return _default
