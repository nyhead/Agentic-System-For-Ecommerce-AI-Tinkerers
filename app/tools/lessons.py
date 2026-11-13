"""Persistent memory of the agents' past mistakes, fed back to them as short lessons."""

import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

# Few lessons, so the prompt stays short and the most useful ones are not drowned out.
MAX_RECALL = 5
MAX_LESSON_LENGTH = 300
# A validation error is quoted in a lesson only when it is this short.
MAX_QUOTED_ERROR = 100

_HEADER = (
    "Lessons from earlier listings in this shop: mistakes made before and corrections from "
    "the shop owner. Follow them so that the same mistakes are not repeated:"
)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass
class Lesson:
    agent: str
    text: str
    count: int
    firstSeen: str
    lastSeen: str


def default_path() -> Path:
    """`LESSONS_PATH`, read per call, else a file in the system temporary directory."""
    configured = os.environ.get("LESSONS_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "commercecrew-lessons.json"


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()), threading.Lock())


class LessonStore:
    """A JSON file of lessons; every error is swallowed so memory can never break an agent."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else default_path()

    def record(self, agent: str, text: str) -> None:
        """Add a lesson, or raise its count when the same lesson for that agent exists."""
        text = " ".join(text.split())[:MAX_LESSON_LENGTH]
        if not text or not agent:
            return
        try:
            path = self.path
            with _lock(path):
                lessons = self._load(path)
                now = datetime.now(UTC).isoformat()
                for lesson in lessons:
                    if lesson.agent == agent and lesson.text == text:
                        lesson.count += 1
                        lesson.lastSeen = now
                        break
                else:
                    lessons.append(Lesson(agent, text, 1, now, now))
                self._save(path, lessons)
        except Exception:
            return

    def all(self) -> list[Lesson]:
        try:
            path = self.path
            with _lock(path):
                return self._load(path)
        except Exception:
            return []

    def recall(self, agent: str, limit: int = MAX_RECALL) -> list[str]:
        """The most frequent, then most recent, lessons for one agent."""
        mine = [lesson for lesson in self.all() if lesson.agent == agent]
        mine.sort(key=lambda lesson: (lesson.count, lesson.lastSeen), reverse=True)
        return [lesson.text for lesson in mine[:limit]]

    @staticmethod
    def _load(path: Path) -> list[Lesson]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        lessons: list[Lesson] = []
        for item in raw:
            try:
                lesson = Lesson(**item)
            except TypeError:
                continue
            if isinstance(lesson.text, str) and isinstance(lesson.count, int):
                lessons.append(lesson)
        return lessons

    @staticmethod
    def _save(path: Path, lessons: list[Lesson]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump([asdict(lesson) for lesson in lessons], handle, ensure_ascii=False)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def record(agent: str, text: str, *, store: LessonStore | None = None) -> None:
    (store or LessonStore()).record(agent, text)


def recall(agent: str, *, store: LessonStore | None = None) -> list[str]:
    return (store or LessonStore()).recall(agent)


def prompt_block(agent: str, *, store: LessonStore | None = None) -> str:
    """Text to append to a user prompt, or "" when there are no lessons for the agent."""
    found = recall(agent, store=store)
    if not found:
        return ""
    return "\n\n" + _HEADER + "".join(f"\n- {text}" for text in found)


def retry_lesson(error: str) -> str:
    """The lesson for a first reply that failed validation, quoting the error when short."""
    error = " ".join(error.split())
    detail = f" (the problem was: {error})" if len(error) <= MAX_QUOTED_ERROR else ""
    return (
        "Reply with exactly one JSON object that matches the schema, with every required "
        f"field: an earlier first reply was invalid{detail}."
    )


def on_retry(agent: str) -> Callable[[str], None]:
    """A hook for `LLMClient.structured` that records the retry lesson for `agent`."""
    return lambda error: record(agent, retry_lesson(error))
