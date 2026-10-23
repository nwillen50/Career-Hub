"""Tests for user company aliases and near-miss merge suggestions (CH-111)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.companies import (
    decide_merge,
    find_companies,
    get_or_create_company,
    pending_merges,
    suggest_merges,
)
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Job, Person, Proposal, Source, utcnow
from careerhub.core.names import company_key, resolve_aliases
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.profile.models import Preferences


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_an_alias_makes_two_names_one_company(session: Session) -> None:
    aliases = resolve_aliases({"Old Name Labs": "New Name"})
    assert company_key("Old Name Labs, Inc.", aliases) == "new name"
    first = get_or_create_company(session, "New Name", aliases)
    again = get_or_create_company(session, "Old Name Labs", aliases)
    assert first is not None and again is first
    assert find_companies(session, "old name labs", aliases) == [first]


def test_chains_are_followed_and_loops_are_rejected() -> None:
    assert resolve_aliases({"a b": "c d", "c d": "e f"}) == {"a b": "e f", "c d": "e f"}
    with pytest.raises(ValueError, match="loop"):
        resolve_aliases({"a b": "c d", "c d": "a b"})
    with pytest.raises(ValueError, match="blank"):
        resolve_aliases({"a b": "  "})
    assert resolve_aliases({"Acme Inc": "acme"}) == {}  # same company already


def test_preferences_check_aliases_when_loaded() -> None:
    assert Preferences(company_aliases={"Old": "New"}).alias_keys() == {"old": "new"}
    with pytest.raises(ValidationError, match="loop"):
        Preferences(company_aliases={"a x": "b x", "b x": "a x"})


def test_ingest_applies_aliases_and_a_near_miss_is_only_suggested(session: Session) -> None:
    source = Source(kind="greenhouse", name="s", url="https://example.com/s", status="approved")
    session.add(source)
    session.commit()

    def job(i: str, company: str) -> NormalizedJob:
        return NormalizedJob(
            company_name=company, external_id=i, title="Engineer", url=f"https://e.com/{i}",
            location="Boston", raw_text="x", raw_payload={},
        )  # fmt: skip

    aliases = resolve_aliases({"Old Name": "New Name"})
    store_jobs(
        session,
        source,
        [job("1", "New Name"), job("2", "Old Name"), job("3", "Acme")],
        aliases=aliases,
    )
    names = [c.name for c in session.scalars(select(Company).order_by(Company.id))]
    assert names == ["New Name", "Acme"]
    store_jobs(session, source, [job("3", "Acme"), job("4", "Acme Robotics")], aliases=aliases)
    assert len(session.scalars(select(Company)).all()) == 3  # nothing merged by itself
    pending = pending_merges(session)
    assert [p.title for p in pending] == ["Merge Acme Robotics into Acme?"]
    store_jobs(session, source, [job("3", "Acme"), job("4", "Acme Robotics")], aliases=aliases)
    assert len(pending_merges(session)) == 1  # not suggested twice


def test_a_suggestion_needs_whole_word_prefix_of_one_or_two_extra_words(session: Session) -> None:
    for name in ("Meta", "Metabase", "Acme", "Acme Robotics Systems Group", "Globex Corp"):
        get_or_create_company(session, name)
    assert suggest_merges(session) == 0
    get_or_create_company(session, "Globex Bio")
    get_or_create_company(session, "Acme Robotics")
    assert (
        suggest_merges(session) == 3
    )  # Acme/Acme Robotics, Acme Robotics/...Systems Group, Globex/Globex Bio


def test_accepting_a_suggestion_merges_jobs_and_people_and_rejecting_changes_nothing(
    session: Session,
) -> None:
    acme = get_or_create_company(session, "Acme")
    robotics = get_or_create_company(session, "Acme Robotics")
    globex, globex_bio = (
        get_or_create_company(session, "Globex"),
        get_or_create_company(session, "Globex Bio"),
    )
    assert acme and robotics and globex and globex_bio
    source = Source(kind="greenhouse", name="s", url="https://example.com/s", status="approved")
    session.add(source)
    session.flush()
    session.add_all([
        Job(company=robotics, source_id=source.id, external_id="1", title="Eng", url="u",
            dedupe_key="k",
            first_seen_at=utcnow(), last_seen_at=utcnow()),
        Person(full_name="Ada Sample", company=robotics),
    ])  # fmt: skip
    session.commit()
    suggest_merges(session)
    merge_id, skip_id = (p.id for p in pending_merges(session))
    assert "Merged Acme Robotics into Acme" in decide_merge(session, merge_id, accept=True)
    assert session.scalars(select(Job)).one().company_id == acme.id
    assert session.scalars(select(Person)).one().company_id == acme.id
    assert session.get(Company, robotics.id) is None
    assert "Rejected" in decide_merge(session, skip_id, accept=False)
    assert session.get(Company, globex_bio.id) is not None
    assert pending_merges(session) == []
    with pytest.raises(LookupError):
        decide_merge(session, merge_id, accept=True)  # already decided
    assert suggest_merges(session) == 0  # a rejected pair is not suggested again


def test_a_suggestion_whose_company_is_gone_merges_nothing(session: Session) -> None:
    get_or_create_company(session, "Acme")
    extra = get_or_create_company(session, "Acme Robotics")
    suggest_merges(session)
    session.delete(extra)
    session.commit()
    (item,) = pending_merges(session)
    assert "already gone" in decide_merge(session, item.id, accept=True)
    assert session.scalars(select(Proposal)).one().status == "accepted"


def test_hub_companies_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    (tmp_path / "alumni.csv").write_text(
        "Name,Company,Group\nAda Sample,Acme,Club\nBen Example,Acme Robotics,Club\n",
        encoding="utf-8",
    )
    assert main(["people", "import-alumni", str(tmp_path / "alumni.csv")], store) == 0
    capsys.readouterr()
    assert main(["companies", "suggestions"], store) == 0
    assert "Merge Acme Robotics into Acme?" in capsys.readouterr().out
    assert main(["companies", "merge", "1"], store) == 0
    assert "Merged" in capsys.readouterr().out
    assert main(["companies", "merge", "1"], store) == 1
    assert main(["companies", "suggestions"], store) == 0
    assert "No merge suggestions." in capsys.readouterr().out
    assert main(["companies", "bogus"], store) == 2
