"""Apify Actor adapters for comparable listings: build each Actor's input, normalize its output.

Input fields and output fields below were taken from each Actor's published input schema and
dataset schema in the Apify Store (checked 13 Nov 2026). Retrieved listing text is untrusted
data: it is copied into fields, never interpreted as instructions.

- caffein.dev/ebay-sold-listings: completed eBay sales. Pay per result, $0.004 at the FREE
  tier. When `isBestOfferAccepted` is true, eBay hides the accepted amount and `soldPrice` is
  the asking price, so those rows get price type "unknown".
- automation-lab/ebay-scraper: active eBay listings (asking prices). Pay per product, $0.003
  at the FREE tier, plus $0.001 per start.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.state import Comparable, Money, PriceType

# Supported target markets: country -> (eBay site for the sold Actor, marketplace code, currency)
MARKETS: dict[str, tuple[str, str, str]] = {
    "US": ("ebay.com", "US", "USD"),
    "GB": ("ebay.co.uk", "UK", "GBP"),
    "DE": ("ebay.de", "DE", "EUR"),
    "FR": ("ebay.fr", "FR", "EUR"),
    "IT": ("ebay.it", "IT", "EUR"),
    "ES": ("ebay.es", "ES", "EUR"),
    "CA": ("ebay.ca", "CA", "CAD"),
    "AU": ("ebay.com.au", "AU", "AUD"),
}

_SYMBOLS = {"$": "USD", "US $": "USD", "£": "GBP", "€": "EUR", "EUR": "EUR", "C $": "CAD"}


@dataclass(frozen=True)
class SearchSpec:
    query: str
    country: str
    max_results: int
    condition: str  # "new", "used" or "any"
    sold_days: int


@dataclass(frozen=True)
class ActorSource:
    key: str
    actor_id: str
    price_per_result_usd: float  # FREE-tier list price, for the spending estimate only
    price_per_start_usd: float
    build_input: Callable[[SearchSpec], dict[str, Any]]
    normalize: Callable[[Mapping[str, Any], str], Comparable | None]

    def estimated_max_usd(self, max_results: int) -> float:
        return round(self.price_per_start_usd + self.price_per_result_usd * max_results, 4)


def parse_amount(value: object) -> float | None:
    """'1.234,56', '1,234.56', '215', 215.0 -> float; anything unreadable -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = re.sub(r"[^\d.,]", "", value)
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _currency(value: object) -> str | None:
    if isinstance(value, str):
        code = value.strip().upper()
        if re.fullmatch(r"[A-Z]{3}", code):
            return code
        return _SYMBOLS.get(value.strip())
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return re.sub(r"\s+", " ", value).strip()[:500]
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _specs(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    specs: dict[str, str] = {}
    for key, raw in list(value.items())[:40]:
        name, text = _text(str(key)), _text(raw if isinstance(raw, str) else str(raw))
        if name and text:
            specs[name[:80]] = text[:200]
    return specs


def _sold_input(spec: SearchSpec) -> dict[str, Any]:
    site = MARKETS[spec.country][0]
    return {
        "keywords": [spec.query],
        "count": spec.max_results,
        "ebaySite": site,
        "daysToScrape": spec.sold_days,
        "sortOrder": "endedRecently",
        "itemCondition": spec.condition,
        "exactMatch": True,
        # Needed for eBay to show which sales were accepted best offers (hidden amounts).
        "includeCompletedListings": True,
    }


def _sold_normalize(item: Mapping[str, Any], ref: str) -> Comparable | None:
    url, title = _text(item.get("url")), _text(item.get("title"))
    if not url or not title:
        return None
    currency = _currency(item.get("soldCurrency"))
    amount = parse_amount(item.get("soldPrice"))
    evidence = {
        key: str(item[key])
        for key in ("soldPrice", "soldPriceMax", "soldCurrency", "listingType", "endedAt")
        if item.get(key) is not None
    }
    evidence["isBestOfferAccepted"] = str(bool(item.get("isBestOfferAccepted")))
    price_type: PriceType = "sold"
    if item.get("isBestOfferAccepted") is True or item.get("listingType") == "best_offer_accepted":
        price_type = "unknown"  # the accepted amount is hidden; soldPrice is the asking price
    elif item.get("soldPriceMax") is not None:
        price_type = "unknown"  # a range across variations, not one sale price
    shipping, note = None, None
    ship_amount = parse_amount(item.get("shippingPrice"))
    ship_currency = _currency(item.get("shippingCurrency")) or currency
    if item.get("shippingType") == "free":
        shipping, note = (Money(amount=0, currency=currency) if currency else None), "Free"
    elif ship_amount is not None and ship_currency:
        shipping = Money(amount=ship_amount, currency=ship_currency)
    else:
        note = "Not stated by the source"
    return Comparable(
        ref=ref,
        source="ebay-sold",
        actorId="caffein.dev/ebay-sold-listings",
        sourceUrl=url,
        retrievedAt=_text(item.get("scrapedAt")) or _now(),
        listingId=_text(item.get("itemId")),
        title=title,
        specs=_specs(item.get("itemSpecifics")),
        condition=_text(item.get("condition")),
        price=Money(amount=amount, currency=currency) if amount is not None and currency else None,
        priceType=price_type,
        shipping=shipping,
        shippingNote=note,
        availability="sold",
        sellerType=_text(item.get("sellerType")),
        endedAt=_text(item.get("endedAt")),
        evidence=evidence,
    )


def _active_input(spec: SearchSpec) -> dict[str, Any]:
    input_: dict[str, Any] = {
        "searchQueries": [spec.query],
        "marketplace": MARKETS[spec.country][1],
        "maxProductsPerSearch": spec.max_results,
        "maxSearchPages": 1,
        "sort": "best_match",
        "listingType": "all",
        "maxRequestRetries": 3,
    }
    return input_


def _active_normalize(item: Mapping[str, Any], ref: str) -> Comparable | None:
    url, title = _text(item.get("url")), _text(item.get("title"))
    if not url or not title:
        return None
    currency = _currency(item.get("currency"))
    amount = parse_amount(item.get("price"))
    listing_type = (_text(item.get("listingType")) or "").lower()
    bids = item.get("bidCount")
    is_auction = "auction" in listing_type or (isinstance(bids, int) and bids > 0)
    shipping_text = _text(item.get("shippingCost"))
    shipping, note = None, shipping_text or "Not stated by the source"
    if shipping_text and "free" in shipping_text.lower() and currency:
        shipping = Money(amount=0, currency=currency)
    evidence = {
        key: str(item[key])
        for key in ("priceString", "currency", "listingType", "bidCount", "shippingCost")
        if item.get(key) is not None
    }
    return Comparable(
        ref=ref,
        source="ebay-active",
        actorId="automation-lab/ebay-scraper",
        sourceUrl=url,
        retrievedAt=_text(item.get("scrapedAt")) or _now(),
        listingId=_text(item.get("itemId")),
        title=title,
        condition=_text(item.get("condition")),
        price=Money(amount=amount, currency=currency) if amount is not None and currency else None,
        priceType="auction_bid" if is_auction else "asking",
        shipping=shipping,
        shippingNote=note,
        availability="available",
        sellerType=_text(item.get("sellerType")),
        evidence=evidence,
    )


SOURCES: dict[str, ActorSource] = {
    "ebay-sold": ActorSource(
        key="ebay-sold",
        actor_id="caffein.dev/ebay-sold-listings",
        price_per_result_usd=0.004,
        price_per_start_usd=0.0001,
        build_input=_sold_input,
        normalize=_sold_normalize,
    ),
    "ebay-active": ActorSource(
        key="ebay-active",
        actor_id="automation-lab/ebay-scraper",
        price_per_result_usd=0.003,
        price_per_start_usd=0.001,
        build_input=_active_input,
        normalize=_active_normalize,
    ),
}
