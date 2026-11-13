"""Research agent tests. Every listing below is synthetic; no test calls Apify or a model."""

import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.agents.research import (
    ResearchConfig,
    ResearchService,
    StaleDraftError,
    classify,
    identify,
)
from app.state import Comparable, ResearchRequest
from app.tools.apify import ApifyClient
from app.tools.llm import LLMClient, LLMError


class NoModel(LLMClient):
    def structured(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("model not used in tests")


def watch_request(**over: Any) -> ResearchRequest:
    data: dict[str, Any] = {
        "productId": "p-omega",
        "title": "Omega Seamaster De Ville — brown strap",
        "description": "Draft description.",
        "category": "Watches",
        "imageSuggestions": [
            {"name": "Brand", "value": "Omega"},
            {"name": "Model line", "value": "Seamaster De Ville"},
            {"name": "Movement", "value": "Automatic"},
        ],
        "country": "DE",
        "currency": "EUR",
        "currentPrice": {"amount": 850, "currency": "EUR"},
    }
    data.update(over)
    return ResearchRequest.model_validate(data)


def sold_row(
    i: int, price: str, title: str = "Omega Seamaster De Ville Automatic Herrenuhr", **over: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "itemId": f"S{i}",
        "url": f"https://www.ebay.de/itm/S{i}",
        "title": title,
        "soldPrice": price,
        "soldCurrency": "EUR",
        "listingType": "buy_it_now",
        "isBestOfferAccepted": False,
        "endedAt": f"2026-10-{10 + i}",
        "condition": "Gebraucht",
    }
    row.update(over)
    return row


FIXTURES = {
    "ebay-sold": [
        sold_row(1, "620"),
        sold_row(2, "700"),
        sold_row(3, "780"),
        sold_row(4, "850"),
        sold_row(5, "990"),
        sold_row(6, "1100", title="Omega Seamaster De Ville vintage automatic"),
        sold_row(7, "500", isBestOfferAccepted=True),
        sold_row(8, "45", title="Strap for Omega Seamaster De Ville"),
        sold_row(9, "300", title="Omega Seamaster De Ville for parts not working"),
        sold_row(10, "650", title="Omega Seamaster De Ville Quartz"),
        sold_row(11, "400", title="Tissot Seastar automatic"),
    ],
    "ebay-active": [
        {
            "itemId": "S2",
            "url": "https://www.ebay.de/itm/S2",
            "title": "dup",
            "price": 700,
            "currency": "EUR",
            "listingType": "Buy It Now",
        },
        {
            "itemId": "A1",
            "url": "https://www.ebay.de/itm/A1",
            "title": "Omega Seamaster De Ville automatic",
            "price": 260,
            "currency": "EUR",
            "listingType": "Auction",
            "bidCount": 3,
        },
    ],
}

CONFIG = ResearchConfig(cache_ttl_secs=3600, max_results_per_source=20)


def fixture_service(**over: Any) -> ResearchService:
    return ResearchService(
        apify=ApifyClient("unused"),
        llm=NoModel(),
        config=CONFIG,
        run_in_background=False,
        fixture_items=FIXTURES,
        **over,
    )


def test_classify_separates_exclusions_and_match_levels() -> None:
    request = watch_request()
    service = fixture_service()
    report = service.start(request)
    excluded = {e.ref: e.reason for e in report.excluded}
    kept = {c.ref: c for c in report.comparables}
    reasons = " | ".join(excluded.values())
    assert "Final price not disclosed" in reasons
    assert "Accessory or part" in reasons
    assert "for parts" in reasons
    assert "Unrelated" in reasons
    assert "Duplicate of" in reasons
    assert "Live auction bid" in reasons
    quartz = next(c for c in kept.values() if "Quartz" in c.title)
    assert quartz.match == "related" and quartz.differences[0].startswith("Listed as quartz")
    assert sum(c.match == "same_model" for c in kept.values()) == 6


def test_watch_report_is_provisional_with_separate_price_change() -> None:
    report = fixture_service().start(watch_request())
    assert report.status == "ready_for_review" and report.dataMode == "fixture"
    rec = report.recommendation
    assert rec is not None and rec.status == "provisional"
    assert rec.basis == "sold:same_model:EUR" and rec.startingPrice == 815.0
    price = next(c for c in report.proposedChanges if c.kind == "price")
    assert price.proposedValue == "815 EUR" and price.requiresConfirmation
    assert "compare-at" in price.rationale
    assert all(c.kind == "content" for c in report.proposedChanges if c.id != "price")
    assert report.profitability is not None and report.profitability.status == "unchecked"
    questions = " ".join(report.merchantQuestions)
    assert "confirm the brand and model" in questions and "reference number" in questions
    description = next(c for c in report.proposedChanges if c.id == "description")
    assert "[Confirm: Brand Omega]" in description.proposedValue
    assert any(p.basis == "hypothesis" for p in report.positioning)


def test_missing_market_asks_instead_of_choosing_and_runs_nothing() -> None:
    calls: list[str] = []

    def transport(*args: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        return 500, b"{}"

    service = ResearchService(
        apify=ApifyClient("t", transport=transport),
        llm=NoModel(),
        config=CONFIG,
        run_in_background=False,
    )
    report = service.start(watch_request(country=None, currency=None))
    assert report.status == "needs_information" and calls == []
    assert "Which market" in report.merchantQuestions[0]


def test_missing_token_fails_honestly_without_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    service = ResearchService(
        apify=ApifyClient(), llm=NoModel(), config=CONFIG, run_in_background=False
    )
    report = service.start(watch_request())
    assert report.status == "failed" and report.dataMode == "live"
    assert report.comparables == [] and report.recommendation is not None
    assert report.recommendation.startingPrice is None
    assert all(s.status == "not_configured" for s in report.sources)


class FakeApify:
    """Routes Apify REST calls; counts paid run starts."""

    def __init__(self) -> None:
        self.starts = 0
        self.polls = 0

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        if method == "POST" and "/runs?" in url:
            self.starts += 1
            run_id = "sold-run" if "ebay-sold" in url else "active-run"
            return 201, json.dumps({"data": {"id": run_id, "status": "RUNNING"}}).encode()
        if "/actor-runs/" in url:
            self.polls += 1
            run_id = url.split("/actor-runs/")[1].split("?")[0]
            data = {
                "id": run_id,
                "status": "SUCCEEDED",
                "defaultDatasetId": f"{run_id}-ds",
                "usageTotalUsd": 0.02,
            }
            return 200, json.dumps({"data": data}).encode()
        if "/datasets/sold-run-ds/" in url:
            return 200, json.dumps(FIXTURES["ebay-sold"]).encode()
        if "/datasets/active-run-ds/" in url:
            return 200, json.dumps(FIXTURES["ebay-active"]).encode()
        return 404, b"{}"


def test_live_path_reports_actual_cost_and_caches_without_new_runs() -> None:
    fake = FakeApify()
    service = ResearchService(
        apify=ApifyClient("t", transport=fake, sleep=lambda _: None),
        llm=NoModel(),
        config=CONFIG,
        run_in_background=False,
    )
    first = service.start(watch_request())
    assert first.status == "ready_for_review" and first.dataMode == "live"
    assert fake.starts == 2
    assert {s.apifyRunId for s in first.sources} == {"sold-run", "active-run"}
    assert first.usage is not None and first.usage.reportedUsd == 0.04
    again = service.start(watch_request())
    assert again.researchId == first.researchId and fake.starts == 2
    refreshed = service.start(watch_request(), refresh=True)
    assert refreshed.researchId != first.researchId and fake.starts == 4


def test_restart_resumes_saved_runs_without_paying_again(tmp_path: Any) -> None:
    state = str(tmp_path / "research.json")
    config = ResearchConfig(cache_ttl_secs=3600, max_results_per_source=20, state_path=state)
    fake = FakeApify()
    client = ApifyClient("t", transport=fake, sleep=lambda _: None)
    first = ResearchService(apify=client, llm=NoModel(), config=config, run_in_background=False)
    report = first.start(watch_request())
    # Simulate a crash after the runs started: mark the job as still collecting.
    saved = json.loads(open(state).read())
    saved[report.researchId]["report"]["status"] = "finding_comparables"
    open(state, "w").write(json.dumps(saved))
    starts_before = fake.starts
    restarted = ResearchService(apify=client, llm=NoModel(), config=config, run_in_background=False)
    restarted.resume_pending()
    assert fake.starts == starts_before
    assert restarted.get(report.researchId).status == "ready_for_review"


def test_content_approval_never_approves_price_and_repeats_are_idempotent() -> None:
    service = fixture_service()
    report = service.start(watch_request())
    current = {"title": report.proposedChanges[0].currentValue}
    with pytest.raises(ValueError, match="separately"):
        service.decide(
            report.researchId,
            kind="content",
            change_ids=["title", "price"],
            approved=True,
            current=current,
        )
    once = service.decide(
        report.researchId, kind="content", change_ids=["title"], approved=True, current=current
    )
    twice = service.decide(
        report.researchId, kind="content", change_ids=["title"], approved=True, current=current
    )
    assert len(once.decisions) == len(twice.decisions) == 1
    assert once.decisions[0].kind == "content" and "price" not in once.decisions[0].changeIds
    assert "nothing was written to Shopify" in once.decisions[0].note


def test_changed_draft_requires_new_review() -> None:
    service = fixture_service()
    report = service.start(watch_request())
    with pytest.raises(StaleDraftError, match="title"):
        service.decide(
            report.researchId,
            kind="content",
            change_ids=["title"],
            approved=True,
            current={"title": "Edited by the merchant"},
        )
    after = service.get(report.researchId)
    assert after.decisions[0].outcome == "needs_new_review"


def test_identity_from_photos_is_provisional() -> None:
    identity = identify(watch_request())
    assert identity.query == "Omega Seamaster De Ville" and not identity.confirmed
    assert "Reference number" in identity.critical_missing


def test_classify_keeps_reference_mismatch_as_related() -> None:
    request = watch_request(
        confirmedFacts=[
            {"name": "Brand", "value": "Omega"},
            {"name": "Model", "value": "Seamaster De Ville"},
            {"name": "Reference number", "value": "135.010"},
        ]
    )
    comp = Comparable.model_validate(
        {
            "ref": "C1",
            "source": "ebay-sold",
            "actorId": "a/b",
            "sourceUrl": "https://x",
            "retrievedAt": "2026-11-13",
            "title": "Omega Seamaster De Ville 166.020",
            "price": {"amount": 800, "currency": "EUR"},
            "priceType": "sold",
        }
    )
    kept, _ = classify([comp], identify(request), request)
    assert kept[0].match == "related"
