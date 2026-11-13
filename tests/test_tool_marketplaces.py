from app.tools.marketplaces import SOURCES, SearchSpec, parse_amount

SOLD = SOURCES["ebay-sold"]
ACTIVE = SOURCES["ebay-active"]


def sold(**over: object) -> dict[str, object]:
    # Synthetic row shaped like caffein.dev/ebay-sold-listings output.
    row: dict[str, object] = {
        "itemId": "1",
        "url": "https://www.ebay.de/itm/1",
        "title": "Omega Seamaster De Ville",
        "soldPrice": "850",
        "soldCurrency": "EUR",
        "listingType": "buy_it_now",
        "isBestOfferAccepted": False,
        "shippingType": None,
        "endedAt": "2026-11-01",
    }
    row.update(over)
    return row


def test_parse_amount_handles_both_decimal_styles() -> None:
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("EUR 850") == 850.0
    assert parse_amount("") is None and parse_amount(None) is None and parse_amount(True) is None


def test_sold_inputs_match_the_published_schema() -> None:
    spec = SearchSpec(query="Omega", country="DE", max_results=7, condition="used", sold_days=90)
    data = SOLD.build_input(spec)
    assert data["keywords"] == ["Omega"] and data["count"] == 7 and data["ebaySite"] == "ebay.de"
    assert ACTIVE.build_input(spec)["marketplace"] == "DE"


def test_best_offer_accepted_is_not_a_verified_sale_price() -> None:
    comp = SOLD.normalize(sold(isBestOfferAccepted=True), "C1")
    assert comp is not None and comp.priceType == "unknown"


def test_variation_range_is_not_one_sale_price() -> None:
    comp = SOLD.normalize(sold(soldPriceMax="900"), "C1")
    assert comp is not None and comp.priceType == "unknown"


def test_completed_auction_is_a_sale_but_a_live_auction_is_a_bid() -> None:
    sold_auction = SOLD.normalize(sold(listingType="auction", bidCount=12), "C1")
    live = ACTIVE.normalize(
        {
            "url": "https://www.ebay.de/itm/2",
            "title": "Omega",
            "price": 300,
            "currency": "EUR",
            "listingType": "Auction",
            "bidCount": 4,
        },
        "C2",
    )
    assert sold_auction is not None and sold_auction.priceType == "sold"
    assert live is not None and live.priceType == "auction_bid"


def test_unknown_shipping_stays_unknown_and_free_is_zero() -> None:
    unknown = SOLD.normalize(sold(), "C1")
    free = SOLD.normalize(sold(shippingType="free"), "C2")
    assert unknown is not None and unknown.shipping is None
    assert unknown.shippingNote == "Not stated by the source"
    assert unknown.taxNote == "Not stated by the source"
    assert free is not None and free.shipping is not None and free.shipping.amount == 0


def test_missing_currency_leaves_no_price() -> None:
    comp = SOLD.normalize(sold(soldCurrency=None), "C1")
    assert comp is not None and comp.price is None
