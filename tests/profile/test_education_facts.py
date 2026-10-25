"""Accepted education facts count for readiness, eligibility and `hub profile show` (CH-212)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import ProfileExtraction
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.storage import open_database, session_factory
from careerhub.profile.eligibility import eligibility_from, parse_graduation
from careerhub.profile.extract import EducationFact
from careerhub.profile.models import Education, ProfileDoc
from careerhub.profile.readiness import missing_items
from careerhub.prompts import load_prompt

PROFILE_NO_EDU = "---\nname: Jane Sample\nskills: []\ninterests: [robotics]\n---\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Aug. 2023 -- May 2027 (Expected)", date(2027, 5, 1)),  # LaTeX \resumeSubheading style
        ("May 2027", date(2027, 5, 1)),
        ("2027-05", date(2027, 5, 1)),
        ("Spring 2027", date(2027, 5, 1)),
        ("Fall 2026", date(2026, 12, 1)),
        ("Expected May 2027", date(2027, 5, 1)),
        ("Sept. 2026", date(2026, 9, 1)),
        ("2027", None),  # a year alone: we won't guess a month
        ("Aug 2023 - Present", None),  # a start date, not a graduation
        ("Class of soon", None),
        ("", None),
        (None, None),
    ],
)
def test_graduation_strings_become_dates(text: str | None, expected: date | None) -> None:
    assert parse_graduation(text) == expected


def test_hostile_text_does_not_crash_the_date_reader() -> None:
    assert parse_graduation("9" * 10_000 + " 2027-13") is None
    assert parse_graduation("May 2027 " * 500) == date(2027, 5, 1)


def test_the_prompt_explains_dates_and_concentrations() -> None:
    text = " ".join(load_prompt("profile_extract", 2).split())
    assert "May 2027" in text and "Joint Concentration" in text and '"level"' in text


def test_a_concentration_school_still_counts_as_a_bachelors() -> None:
    fact = EducationFact(
        school="Harvard College", degree="Joint Concentration: Physics", level="bachelor",
        graduation="May 2027",
    )  # fmt: skip
    got = eligibility_from(ProfileDoc(name="J"), None, [fact])
    assert (got.highest_degree, got.graduation) == ("bachelor", date(2027, 5, 1))


def test_profile_md_wins_over_resume_facts() -> None:
    doc = ProfileDoc(
        name="J",
        education=[Education(school="U", degree="BS CS", graduation=date(2028, 5, 1))],
    )
    fact = EducationFact(school="U", degree="BS CS", graduation="May 2027")
    assert eligibility_from(doc, None, [fact]).graduation == date(2028, 5, 1)
    # ...but a resume date fills a gap when profile.md has none.
    bare = ProfileDoc(name="J", education=[Education(school="U", degree="BS CS")])
    assert eligibility_from(bare, None, [fact]).graduation == date(2027, 5, 1)


def accept(session, education: list[dict], skills: list[str] | None = None) -> None:  # type: ignore[no-untyped-def]
    session.add(
        ProfileExtraction(
            kind="resume",
            file_sha256="b" * 64,
            prompt_version=2,
            status="accepted",
            facts={"education": education, "skills": skills or []},
        )
    )
    session.commit()


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    (tmp_path / "profile").mkdir()
    (tmp_path / "inputs" / "resume").mkdir(parents=True)
    (tmp_path / "profile" / "profile.md").write_text(PROFILE_NO_EDU)
    (tmp_path / "profile" / "preferences.yaml").write_text(
        "hard_constraints:\n  locations: ['Boston, MA']\n"
    )
    (tmp_path / "profile" / "rubric.yaml").write_text(
        "version: 1\ncriteria:\n  - {name: f, weight: 1, description: d}\n"
    )
    (tmp_path / "inputs" / "resume" / "resume.txt").write_text("Built things.")
    return tmp_path


def test_accepted_education_satisfies_readiness(data: Path) -> None:
    store = InMemoryKeyStore()
    engine = open_database(store)
    migrate(engine)
    with session_factory(engine)() as session:
        assert any(m.startswith("Education") for m in missing_items(session, 0))
        accept(session, [{"school": "Harvard College", "graduation": "Aug. 2023 -- May 2027"}])
        assert not any(m.startswith("Education") for m in missing_items(session, 0))


def test_an_unparseable_graduation_stays_missing(data: Path) -> None:
    engine = open_database(InMemoryKeyStore())
    migrate(engine)
    with session_factory(engine)() as session:
        accept(session, [{"school": "Harvard College", "graduation": "someday"}])
        assert any(m.startswith("Education") for m in missing_items(session, 0))


def test_profile_show_counts_accepted_facts(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryKeyStore()
    engine = open_database(store)
    migrate(engine)
    with session_factory(engine)() as session:
        accept(
            session,
            [{"school": "Harvard College", "degree": "Physics", "graduation": "May 2027"}],
            ["Python", "SQL"],
        )
    assert main(["profile", "show"], store) == 0
    out = capsys.readouterr().out
    assert "Skills: Python, SQL (from your resume)" in out
    assert "Education: Physics, Harvard College, May 2027 (from your resume)" in out
    assert "Skills: 0 of" not in out and "Education: add your school" not in out
