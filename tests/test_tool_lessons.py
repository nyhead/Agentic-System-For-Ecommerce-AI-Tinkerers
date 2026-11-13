import json
import threading

import pytest

from app.tools import lessons
from app.tools.lessons import MAX_RECALL, LessonStore


@pytest.fixture(autouse=True)
def lessons_path(tmp_path, monkeypatch):
    path = tmp_path / "lessons.json"
    monkeypatch.setenv("LESSONS_PATH", str(path))
    return path


def test_record_and_recall_per_agent(tmp_path):
    store = LessonStore(tmp_path / "l.json")
    store.record("product", "Keep titles short.")
    store.record("marketing", "No hashtags.")
    assert store.recall("product") == ["Keep titles short."]
    assert store.recall("marketing") == ["No hashtags."]
    assert store.recall("nobody") == []


def test_duplicate_raises_the_count_instead_of_adding(tmp_path):
    store = LessonStore(tmp_path / "l.json")
    store.record("product", "Keep titles short.")
    store.record("product", "  Keep   titles short. ")
    [lesson] = store.all()
    assert lesson.count == 2
    assert lesson.firstSeen <= lesson.lastSeen


def test_recall_orders_by_count_then_recency_and_is_capped(tmp_path):
    store = LessonStore(tmp_path / "l.json")
    for i in range(MAX_RECALL + 3):
        store.record("product", f"lesson {i}")
    store.record("product", "lesson 0")
    found = store.recall("product")
    assert len(found) == MAX_RECALL
    assert found[0] == "lesson 0"
    assert found[1] == f"lesson {MAX_RECALL + 2}"


def test_persists_across_store_instances(tmp_path):
    path = tmp_path / "l.json"
    LessonStore(path).record("product", "Keep titles short.")
    assert LessonStore(path).recall("product") == ["Keep titles short."]


def test_default_store_follows_the_environment(lessons_path):
    lessons.record("product", "From env.")
    assert json.loads(lessons_path.read_text())[0]["text"] == "From env."
    assert lessons.recall("product") == ["From env."]


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', '[{"wrong": 1}, 5]', ""])
def test_corrupt_file_is_treated_as_empty(tmp_path, content):
    path = tmp_path / "l.json"
    path.write_text(content)
    store = LessonStore(path)
    assert store.recall("product") == []
    store.record("product", "Recovered.")
    assert store.recall("product") == ["Recovered."]


def test_unwritable_path_is_harmless(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = LessonStore(blocker / "sub" / "l.json")
    store.record("product", "Lost.")
    assert store.recall("product") == []
    assert lessons.prompt_block("product", store=store) == ""


def test_concurrent_records_do_not_corrupt_the_file(tmp_path):
    path = tmp_path / "l.json"
    store = LessonStore(path)

    def work(n):
        for i in range(20):
            store.record("product", f"lesson {n % 3}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = json.loads(path.read_text())
    assert sorted(item["text"] for item in data) == ["lesson 0", "lesson 1", "lesson 2"]
    assert sum(item["count"] for item in data) == 120


def test_prompt_block_is_empty_without_lessons_and_lists_them_otherwise(tmp_path):
    store = LessonStore(tmp_path / "l.json")
    assert lessons.prompt_block("product", store=store) == ""
    store.record("product", "Keep titles short.")
    block = lessons.prompt_block("product", store=store)
    assert block.startswith("\n\nLessons from earlier listings in this shop")
    assert "shop owner" in block
    assert block.endswith("\n- Keep titles short.")


def test_retry_lesson_quotes_only_a_short_error():
    assert "(the problem was: title: Field required)" in lessons.retry_lesson(
        "title: Field required"
    )
    assert "problem was" not in lessons.retry_lesson("x" * 500)
