from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Person
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.network import find_company, warm_paths

NOW = datetime(2026, 10, 21, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.fixture
def acme(session: Session) -> Company:
    company = Company(name="Acme Corp", name_key="acme corp")
    session.add(company)
    session.commit()
    return company


def add(session: Session, company: Company, name: str, **kw: object) -> Person:
    person = Person(full_name=name, company=company, **kw)  # type: ignore[arg-type]
    session.add(person)
    session.commit()
    return person


def test_your_own_words_beat_alumni_beat_a_plain_connection(
    session: Session, acme: Company
) -> None:
    add(session, acme, "Cy Connection", origin="linkedin_export")
    add(session, acme, "Al Alum", origin="alumni_list", how_known="alumni: Example University")
    add(session, acme, "Mo Mentor", origin="linkedin_export", how_known="former manager")
    paths = warm_paths(session, acme.id, now=NOW)
    assert [p.name for p in paths] == ["Mo Mentor", "Al Alum", "Cy Connection"]
    assert [p.strength for p in paths] == [4, 2, 1]
    assert paths[0].reasons == ["you noted: former manager", "LinkedIn connection"]
    assert paths[1].reasons == ["alumni: Example University"]


def test_recent_contact_and_recruiters_add_points(session: Session, acme: Company) -> None:
    recent = add(
        session,
        acme,
        "Ray Recent",
        origin="linkedin_export",
        last_contact_at=NOW - timedelta(days=30),
    )
    old = add(
        session,
        acme,
        "Olly Old",
        origin="linkedin_export",
        last_contact_at=NOW - timedelta(days=800),
    )
    recruiter = add(
        session, acme, "Rae Recruiter", origin="linkedin_export", title="Senior Technical Recruiter"
    )
    by_name = {p.name: p for p in warm_paths(session, acme.id, now=NOW)}
    assert by_name[recent.full_name].strength == 2
    assert "last contact 30 days ago" in by_name[recent.full_name].reasons
    assert by_name[old.full_name].strength == 1
    assert by_name[recruiter.full_name].reasons[-1] == "recruiter"


def test_alumni_tag_mixed_with_the_users_words_counts_both(session: Session, acme: Company) -> None:
    add(session, acme, "Ann Both", how_known="former teammate; alumni: Robotics Club")
    path = warm_paths(session, acme.id, now=NOW)[0]
    assert path.strength == 5 and path.reasons == [
        "you noted: former teammate",
        "alumni: Robotics Club",
    ]


def test_people_with_no_facts_still_appear_last_and_limit_applies(
    session: Session, acme: Company
) -> None:
    add(session, acme, "Zed Unknown")
    add(session, acme, "Amy Tagged", how_known="friend")
    add(session, acme, "Bob Tagged", how_known="friend")
    paths = warm_paths(session, acme.id, limit=3, now=NOW)
    assert [p.name for p in paths] == ["Amy Tagged", "Bob Tagged", "Zed Unknown"]
    assert paths[2].strength == 0 and paths[2].reasons == []
    assert len(warm_paths(session, acme.id, limit=1, now=NOW)) == 1


def test_only_people_at_that_company_count(session: Session, acme: Company) -> None:
    other = Company(name="Globex", name_key="globex")
    session.add(other)
    add(session, other, "Gil Elsewhere", how_known="friend")
    assert warm_paths(session, acme.id, now=NOW) == []


def test_find_company_prefers_exact_then_contains(session: Session, acme: Company) -> None:
    session.add(Company(name="Acme Corp Labs", name_key="acme corp labs"))
    session.commit()
    assert find_company(session, "  ACME   corp ").name == "Acme Corp"  # type: ignore[union-attr]
    assert find_company(session, "labs").name == "Acme Corp Labs"  # type: ignore[union-attr]
    assert find_company(session, "zzz") is None


def test_hub_paths_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    csv_path = tmp_path / "a.csv"
    csv_path.write_text("Name,Company,Group\nAda Sample,Acme,Club\n", encoding="utf-8")
    main(["people", "import-alumni", str(csv_path)], store)
    capsys.readouterr()
    assert main(["paths", "acme"], store) == 0
    assert "Ada Sample" in capsys.readouterr().out
    assert main(["paths", "nowhere"], store) == 1
