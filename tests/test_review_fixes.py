"""Regression tests for the bugs an independent review found (CH-78). Each failed before its fix."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings, save_settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.cache import ResponseCache
from careerhub.llm.fake import FakeProvider
from careerhub.llm.setup import build_router
from careerhub.llm.validated import complete_validated
from careerhub.modules.jobs import (
    FetchError,
    NormalizedJob,
    approve_source,
    ingest_greenhouse,
    propose_source,
    store_jobs,
)
from careerhub.modules.jobs.extract import extract_and_save
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.scoring import ranked_jobs, score_job
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Criterion, Rubric

NOW = datetime(2026, 10, 21, tzinfo=UTC)


@pytest.fixture
def sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)


@pytest.fixture
def session(sessions: sessionmaker[Session]) -> Session:
    return sessions()


class Answer(BaseModel):
    value: int


def ask(prompt: str = "p") -> LLMRequest:
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PUBLIC)


# 1. An invalid answer must never be cached.
def test_an_invalid_answer_is_not_cached_so_a_later_run_can_succeed(
    sessions: sessionmaker[Session],
) -> None:
    provider = FakeProvider("m", replies=['{"value": ', '{"value": ', '{"value": 7}'])
    router = Router([provider], cache=ResponseCache(sessions))
    with pytest.raises(Exception, match="No valid answer"):
        complete_validated(router, ask(), Answer, ["m"])  # both attempts truncated
    assert complete_validated(router, ask(), Answer, ["m"]).value == 7  # next run recovers
    assert complete_validated(router, ask(), Answer, ["m"]).value == 7  # and now it's cached
    assert len(provider.requests) == 3


def test_an_invalid_answer_already_in_the_cache_is_replaced(
    sessions: sessionmaker[Session],
) -> None:
    cache = ResponseCache(sessions)
    bad = FakeProvider("m", replies=["not json"])
    Router([bad], cache=cache).complete(ask(), "m")  # an old run stored garbage
    good = FakeProvider("m", replies=['{"value": 1}'])
    assert complete_validated(Router([good], cache=cache), ask(), Answer, ["m"]).value == 1
    assert len(good.requests) == 1


# 2. A duplicate must stay visible when its original closes.
def seed_two_sources(session: Session) -> tuple[Source, Source]:
    a = Source(kind="greenhouse", name="a", url="https://a", status="approved")
    b = Source(kind="greenhouse", name="b", url="https://b", status="approved")
    session.add_all([a, b])
    session.commit()
    posting = NormalizedJob("Acme", "1", "Engineer", "https://x/1", "Boston, MA", "Text")
    store_jobs(session, a, [posting], NOW)
    store_jobs(
        session, b, [NormalizedJob("Acme", "9", "Engineer", "https://y/9", "Boston, MA", "T")], NOW
    )
    return a, b


RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(name="Jane", skills=["Python"])
SCORE = json.dumps({"criteria": [{"name": "field", "score": 5}], "explanation": "ok"})


def score_all(session: Session, provider: FakeProvider | None = None) -> None:
    router = Router([provider or FakeProvider("m", replies=[SCORE])])
    for job in session.scalars(select(Job)):
        assess_filters(session, job, HardConstraints())
        score_job(session, router, job, DOC, RUBRIC, ["m"])


def test_a_duplicate_is_shown_once_its_original_closes(session: Session) -> None:
    a, b = seed_two_sources(session)
    score_all(session)
    assert [job.source_id for job, _ in ranked_jobs(session)] == [a.id]  # one copy while both open
    store_jobs(session, a, [], NOW)  # source A stops listing it
    assert [job.source_id for job, _ in ranked_jobs(session)] == [b.id]


# 3. Stale scores and extraction.
def test_a_score_is_redone_when_the_profile_changes(session: Session) -> None:
    source = Source(kind="greenhouse", name="a", url="https://a", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", None, "Text")], NOW)
    job = session.scalars(select(Job)).one()
    assess_filters(session, job, HardConstraints())
    provider = FakeProvider("m", replies=[SCORE])
    router = Router([provider])
    assert score_job(session, router, job, DOC, RUBRIC, ["m"]) is not None
    assert score_job(session, router, job, DOC, RUBRIC, ["m"]) is None
    changed = ProfileDoc(name="Jane", skills=["Python", "Rust"])
    assert score_job(session, router, job, changed, RUBRIC, ["m"]) is not None
    job.raw_text = "A very different posting"
    assert score_job(session, router, job, changed, RUBRIC, ["m"]) is not None


def test_extraction_is_redone_when_the_posting_text_changes(session: Session) -> None:
    source = Source(kind="greenhouse", name="a", url="https://a", status="approved")
    session.add(source)
    session.commit()
    store_jobs(
        session, source, [NormalizedJob("Acme", "1", "Engineer", "u", None, "Version one")], NOW
    )
    job = session.scalars(select(Job)).one()
    provider = FakeProvider("m", replies=[json.dumps({"level": "entry"})])
    router = Router([provider])
    assert extract_and_save(session, router, job, ["m"]) is not None
    assert extract_and_save(session, router, job, ["m"]) is None
    job.raw_text = "Version two, now senior"
    assert extract_and_save(session, router, job, ["m"]) is not None


# 4. An empty listing must not wipe a source that had open jobs.
def board(jobs: list[dict[str, Any]]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=json.dumps({"jobs": jobs}))
        )
    )


def test_an_empty_listing_does_not_close_every_job(session: Session) -> None:
    source = propose_source(
        session, "greenhouse", "Acme", "https://boards.greenhouse.io/acme", {"board": "acme"}
    )
    approve_source(session, source.id)
    job = {
        "id": 1,
        "title": "Engineer",
        "absolute_url": "https://x/1",
        "location": {"name": "Boston"},
    }
    ingest_greenhouse(session, source, board([job]))
    with pytest.raises(FetchError, match="no jobs"):
        ingest_greenhouse(session, source, board([]))
    assert session.scalars(select(Job)).one().status == "new" and source.health == "failing"


def test_an_empty_listing_is_fine_for_a_source_with_no_open_jobs(session: Session) -> None:
    source = propose_source(
        session, "greenhouse", "Acme", "https://boards.greenhouse.io/acme", {"board": "acme"}
    )
    approve_source(session, source.id)
    assert ingest_greenhouse(session, source, board([])).new == 0


# 5. A cloud provider with no prices must be reported, not silently free.
def test_a_cloud_provider_without_prices_is_reported(sessions: sessionmaker[Session]) -> None:
    settings = Settings.model_validate(
        {"providers": [{"name": "cloud", "kind": "openai", "model": "m"}]}
    )
    store = InMemoryKeyStore()
    store.set("api-key-cloud", "K")
    setup = build_router(settings, store, sessions)
    assert setup.built == ("cloud",)
    assert any("no prices" in p and "budget" in p for p in setup.problems)


# 6. Alumni tags are matched as whole tags.
def test_a_longer_group_name_does_not_hide_a_shorter_one(session: Session) -> None:
    from careerhub.modules.network import import_alumni

    def run(text: str, tmp: Path) -> None:
        file = tmp / "a.csv"
        file.write_text("Name,Company,Group\n" + text, encoding="utf-8")
        import_alumni(session, file)

    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        run("Ada Sample,Acme,MIT Sloan\n", Path(folder))
        run("Ada Sample,Acme,MIT\n", Path(folder))
    from careerhub.core.models import Person

    assert session.scalars(select(Person)).one().how_known == "alumni: MIT Sloan; alumni: MIT"


# 7. Private files are never briefly readable; board names are validated.
def test_the_settings_file_is_private_from_the_moment_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    real_open = os.open

    def spy(path: Any, flags: int, mode: int = 0o777, *a: Any, **k: Any) -> int:
        if str(path).endswith("settings.json"):
            seen.append(mode)
        return real_open(path, flags, mode, *a, **k)

    monkeypatch.setattr(os, "open", spy)
    path = save_settings(Settings(), tmp_path / "settings.json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and seen == [0o600]


@pytest.mark.parametrize("bad", ["..", "a/b", "a b", "", "a?x=1"])
def test_board_names_are_validated(session: Session, bad: str) -> None:
    source = propose_source(
        session,
        "greenhouse",
        "X",
        "https://boards.greenhouse.io/x" + bad.replace("/", "-"),
        {"board": bad},
    )
    approve_source(session, source.id)
    with pytest.raises(FetchError, match="board"):
        ingest_greenhouse(session, source, board([]))
