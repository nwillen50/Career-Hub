"""Separate wrongly merged companies by hand (CH-174)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.companies import (
    decide_merge,
    find_companies,
    get_or_create_company,
    list_merges,
    merge_companies,
    pending_merges,
    split_company,
    suggest_merges,
)
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Job, Person, Source, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def source(session: Session, name: str) -> Source:
    row = Source(kind="greenhouse", name=name, url=f"https://x/{name}", status="approved")
    session.add(row)
    session.flush()
    return row


def world(session: Session) -> Company:
    """Acme (job 1, person Ann) and Acme Robotics (job 2, person Bo) merged into Acme."""
    gh, lever = source(session, "gh"), source(session, "lever")
    store_jobs(session, gh, [NormalizedJob("Acme", "1", "Engineer", "u1", "Boston", "t")])
    store_jobs(
        session, lever, [NormalizedJob("Acme Robotics", "2", "Engineer", "u2", "Boston", "t")]
    )
    acme, robotics = find_companies(session, "Acme")[0], find_companies(session, "Acme Robotics")[0]
    assert acme is not robotics
    session.add_all(
        [
            Person(company_id=acme.id, full_name="Ann"),
            Person(company_id=robotics.id, full_name="Bo"),
        ]
    )
    session.commit()
    suggest_merges(session)
    (item,) = pending_merges(session)
    decide_merge(session, item.id, accept=True)
    return acme


def test_merge_then_split_leaves_both_companies_as_before(session: Session) -> None:
    acme = world(session)
    first, second = session.scalars(select(Job).order_by(Job.id)).all()
    assert second.duplicate_of_id == first.id and len(session.scalars(select(Company)).all()) == 1
    message = split_company(session, acme, "Acme Robotics")
    assert "1 job(s) moved back" in message
    names = {c.name: c for c in session.scalars(select(Company))}
    assert set(names) == {"Acme", "Acme Robotics"}
    session.refresh(first)
    session.refresh(second)
    assert (first.company_id, second.company_id) == (names["Acme"].id, names["Acme Robotics"].id)
    assert first.dedupe_key != second.dedupe_key
    assert first.duplicate_of_id is None and second.duplicate_of_id is None
    people = {p.full_name: p.company_id for p in session.scalars(select(Person))}
    assert people == {"Ann": names["Acme"].id, "Bo": names["Acme Robotics"].id}
    assert acme.merged_keys is None and acme.merge_log is None


def test_the_next_ingest_keeps_them_apart_and_they_are_not_suggested_again(
    session: Session,
) -> None:
    acme = world(session)
    split_company(session, acme, "Acme Robotics")
    store_jobs(
        session,
        source(session, "ashby"),
        [NormalizedJob("Acme Robotics", "3", "Analyst", "u3", "Boston", "t")],
    )
    assert len(session.scalars(select(Company)).all()) == 2
    third = session.scalars(select(Job).where(Job.external_id == "3")).one()
    assert third.company.name == "Acme Robotics"
    assert suggest_merges(session) == 0 and pending_merges(session) == []


def test_a_job_that_moved_elsewhere_since_stays_put(session: Session) -> None:
    acme = world(session)
    other = get_or_create_company(session, "Globex")
    assert other
    moved = session.scalars(select(Job).where(Job.external_id == "2")).one()
    moved.company_id = other.id
    session.commit()
    assert "0 job(s) moved back" in split_company(session, acme, "Acme Robotics")
    session.refresh(moved)
    assert moved.company_id == other.id


def test_splitting_a_name_that_was_never_merged_is_refused(session: Session) -> None:
    acme = world(session)
    with pytest.raises(LookupError):
        split_company(session, acme, "Globex")


def test_an_older_merge_without_a_log_falls_back_to_the_source_names(session: Session) -> None:
    acme = world(session)
    acme.merge_log = None  # a merge made before CH-174
    for job in session.scalars(select(Job)):
        job.raw_payload = {"company_name": "Acme Robotics" if job.external_id == "2" else "Acme"}
    session.commit()
    message = split_company(session, acme, "Acme Robotics")
    assert "1 job(s) moved back" in message and "people stayed" in message
    assert {p.company_id for p in session.scalars(select(Person))} == {acme.id}


def test_merges_lists_each_merge_and_the_command_asks_before_splitting(
    session: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    acme = world(session)
    ((owner, entry),) = list_merges(session)
    assert owner is acme and entry["job_ids"] and entry["person_ids"]
    from careerhub.core.secrets import InMemoryKeyStore

    store = InMemoryKeyStore()
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    from careerhub import cli

    monkeypatch.setattr(cli, "_with_session", lambda _store, action: action(session))
    assert main(["companies", "merges"], store) == 0
    assert "Acme Robotics was merged into Acme" in capsys.readouterr().out
    assert main(["companies", "split", "Acme", "Acme Robotics"], store) == 0
    assert (
        "Nothing changed" in capsys.readouterr().out
        and len(session.scalars(select(Company)).all()) == 1
    )
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert main(["companies", "split", "Acme", "Acme Robotics"], store) == 0
    assert "Split Acme Robotics" in capsys.readouterr().out
    assert len(session.scalars(select(Company)).all()) == 2


def test_splitting_a_company_that_had_absorbed_another_hands_the_log_back(
    session: Session,
) -> None:
    """Review finding: merge A into B, then B into C; splitting B out keeps A splittable."""
    a, b, c = (
        get_or_create_company(session, n)
        for n in ("Zeta", "Zeta Foundry", "Zeta Foundry Collective")
    )
    assert a and b and c and len({a.id, b.id, c.id}) == 3
    for name, company in (("A job", a), ("B job", b)):
        session.add(Job(company_id=company.id, source_id=source(session, name).id,
                        external_id=name, title=name, url="u", dedupe_key=name,
                        first_seen_at=utcnow(), last_seen_at=utcnow()))  # fmt: skip
    session.commit()
    merge_companies(session, b, a)
    merge_companies(session, c, b)
    session.commit()
    assert {e["name_key"] for e in c.merge_log or []} == {a.name_key, b.name_key}
    split_company(session, c, "Zeta Foundry")
    restored = find_companies(session, "Zeta Foundry")[0]
    assert restored is not c
    assert [e["name_key"] for e in restored.merge_log or []] == ["zeta"]
    assert c.merge_log is None and c.merged_keys is None
    assert "zeta" in (restored.merged_keys or [])
    split_company(session, restored, "Zeta")  # A can still be split out of B
    assert {co.name for co in session.scalars(select(Company))} == {
        "Zeta",
        "Zeta Foundry",
        "Zeta Foundry Collective",
    }
