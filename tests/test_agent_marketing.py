import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents.marketing import (
    AD_COPY_MAX,
    META_DESCRIPTION_MAX,
    REQUEST_TIMEOUT_SECONDS,
    SEO_TITLE_MAX,
    _fit,
    write_marketing,
)
from app.state import MarketingResult, ProductRecord
from app.tools.llm import LLMClient, LLMError

ROOT = Path(__file__).resolve().parents[1]

GOOD_COPY = {
    "seoTitle": "Blue Ceramic Mug with Glazed Finish and Easy-Grip Handle",
    "metaDescription": (
        "A blue ceramic mug with a glazed finish and an easy-grip handle. "
        "A simple everyday mug for your morning coffee or tea."
    ),
    "socialAds": [
        {"channel": "Instagram", "copy": "Deep blue with a glazed finish. Meet our new mug."},
        {"channel": "Facebook", "copy": "An easy-grip handle for your everyday coffee or tea."},
    ],
}


def fixture_product():
    return ProductRecord.model_validate_json((ROOT / "fixtures" / "product.json").read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"body": json.loads(body), "timeout": timeout})
        return self.responses.pop(0)


def reply(payload, status=200):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return status, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def make_client(*responses):
    transport = FakeTransport(*responses)
    client = LLMClient(
        "https://llm.example/v1",
        "sk-test",
        "some/model",
        transport=transport,
        sleep=lambda seconds: None,
    )
    return client, transport


def with_ads(*ads):
    return {**GOOD_COPY, "socialAds": [{"channel": c, "copy": t} for c, t in ads]}


def test_fixture_product_gives_valid_marketing_result():
    client, transport = make_client(reply(GOOD_COPY))
    result = write_marketing(fixture_product(), llm=client)
    assert isinstance(result, MarketingResult)
    assert result.model_dump() == GOOD_COPY
    assert len(transport.calls) == 1


def test_prompt_carries_product_facts_and_no_invented_facts_rule():
    client, transport = make_client(reply(GOOD_COPY))
    write_marketing(fixture_product(), llm=client)
    body = transport.calls[0]["body"]
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "every claim must be true" in system["content"]
    prompt = user["content"]
    assert "Blue Ceramic Mug" in prompt
    assert "A blue ceramic mug with a glazed finish and an easy-grip handle." in prompt
    assert "Use only facts stated in the product title and description." in prompt
    for banned in ("materials", "dimensions", "prices", "discounts", "shipping", "health"):
        assert banned in prompt
    assert "publishing false claims under their own name" in prompt
    assert "never pad the copy with extra claims" in prompt
    assert '"Instagram"' in prompt
    assert '"Facebook"' in prompt
    assert '"copy"' in prompt  # the JSON Schema the adapter appends uses the alias
    assert body["temperature"] == 0.5


def test_default_client_is_built_at_call_time_with_short_timeout(monkeypatch):
    client, transport = make_client(reply(GOOD_COPY))
    built = []

    def build_client(**kwargs):
        built.append(kwargs)
        return client

    monkeypatch.setattr("app.agents.marketing.LLMClient", build_client)
    assert built == []
    write_marketing(fixture_product())
    assert built == [{"timeout": REQUEST_TIMEOUT_SECONDS}]
    assert REQUEST_TIMEOUT_SECONDS * 4 <= 60.0


def test_over_long_output_is_cut_to_the_limits():
    long_copy = {
        "seoTitle": "Blue Ceramic Mug with a Glazed Finish and an Easy-Grip Handle for Coffee",
        "metaDescription": "A blue ceramic mug with a glazed finish. " + "Glazed and blue " * 20,
        "socialAds": [
            {"channel": "Instagram", "copy": "Blue, glazed and easy to hold " * 10},
            {"channel": "Facebook", "copy": "The handle is easy to grip every morning. " * 10},
        ],
    }
    client, _ = make_client(reply(long_copy))
    result = write_marketing(fixture_product(), llm=client)
    assert result.seoTitle == "Blue Ceramic Mug with a Glazed Finish and an Easy-Grip…"
    assert len(result.seoTitle) <= SEO_TITLE_MAX
    assert len(result.metaDescription) <= META_DESCRIPTION_MAX
    assert result.metaDescription.endswith("blue…")
    instagram, facebook = result.socialAds
    assert len(instagram.text) <= AD_COPY_MAX
    assert len(facebook.text) <= AD_COPY_MAX
    assert facebook.text.endswith("every morning.")


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("Short enough.", 20, "Short enough."),
        ("  spaced \n out  ", 20, "spaced out"),
        ("One two three four", 10, "One two…"),
        ("One two, three four", 10, "One two…"),
        ("First part. Second part here.", 20, "First part."),
        ("Hi. A long second sentence follows", 20, "Hi. A long second…"),
        ("Version 2.5 is here now", 12, "Version 2.5…"),
        ("Supercalifragilistic", 10, "Supercali…"),
    ],
)
def test_fit_trims_at_sentence_or_word_boundary(text, limit, expected):
    assert _fit(text, limit) == expected
    assert len(expected) <= limit


def test_dashboard_json_uses_contract_field_names():
    client, _ = make_client(reply(GOOD_COPY))
    payload = json.loads(write_marketing(fixture_product(), llm=client).model_dump_json())
    assert list(payload) == ["seoTitle", "metaDescription", "socialAds"]
    assert [list(ad) for ad in payload["socialAds"]] == [["channel", "copy"], ["channel", "copy"]]
    assert payload["socialAds"][0] == GOOD_COPY["socialAds"][0]


def test_model_failure_propagates_as_llm_error():
    client, transport = make_client((502, b"bad gateway"), (502, b"bad gateway"))
    with pytest.raises(LLMError, match="HTTP 502"):
        write_marketing(fixture_product(), llm=client)
    assert len(transport.calls) == 2


def test_reply_that_never_matches_the_contract_raises_llm_error():
    one_ad = {**GOOD_COPY, "socialAds": GOOD_COPY["socialAds"][:1]}
    client, transport = make_client(reply(one_ad), reply("sorry, no"))
    with pytest.raises(LLMError, match="MarketingResult after a retry"):
        write_marketing(fixture_product(), llm=client)
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "copy",
    [
        with_ads(("Instagram", "Blue mug."), ("instagram", "New mug.")),
        with_ads(("Instagram", "Blue mug."), ("Facebook", "blue  mug.")),
        with_ads(("Instagram", "   "), ("Facebook", "Blue mug.")),
        {**GOOD_COPY, "seoTitle": " "},
    ],
    ids=["same-channel", "same-copy", "empty-copy", "empty-title"],
)
def test_unusable_copy_raises_instead_of_reaching_the_dashboard(copy):
    client, _ = make_client(reply(copy))
    with pytest.raises(LLMError):
        write_marketing(fixture_product(), llm=client)


def test_import_needs_no_credentials():
    code = "import app.agents.marketing as m; print(m.write_marketing.__name__)"
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "write_marketing"
