"""Tests for the readiness gate (CH-102). All data is made up."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import ProfileExtraction
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.profile.readiness import (
    current_readiness,
    missing_items,
    scoring_since,
    set_scoring,
)
from careerhub.web.shortlist import ShortlistData, gather_shortlist, render_shortlist

PROFILE = """---
name: Jane Sample
education:
  - school: Example University
    degree: BS Physics
    graduation: 2027-05-01
skills: [Python, SQL, Git, Linux, C]
interests: [robotics]
---
"""
PREFS = "hard_constraints:\n  locations: ['Boston, MA']\n"
RUBRIC = "version: 1\ncriteria:\n  - {name: field, weight: 1, description: d}\n"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    (tmp_path / "profile").mkdir()
    (tmp_path / "inputs" / "resume").mkdir(parents=True)
    return tmp_path


def fill(data: Path, **parts: str | None) -> None:
    files = {
        "profile/profile.md": PROFILE,
        "profile/preferences.yaml": PREFS,
        "profile/rubric.yaml": RUBRIC,
        "inputs/resume/resume.txt": "Built a Python tool.",
    }
    files.update({k.replace("__", "/").replace("_dot_", "."): v for k, v in parts.items()})  # type: ignore[misc]
    for name, text in files.items():
        path = data / name
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def test_a_complete_profile_has_nothing_missing(data: Path) -> None:
    fill(data)
    assert missing_items(None) == []


def test_each_checklist_item_is_reported_when_absent(data: Path) -> None:
    fill(data)
    cases = {
        "profile/profile.md": PROFILE.replace("graduation: 2027-05-01", ""),
        "profile/preferences.yaml": "hard_constraints: {}\n",
        "profile/rubric.yaml": None,
        "inputs/resume/resume.txt": None,
    }
    expected = ["graduation", "locations", "rubric", "resume"]
    for (name, text), word in zip(cases.items(), expected, strict=True):
        fill(data)  # reset
        path = data / name
        path.unlink() if text is None else path.write_text(text, encoding="utf-8")
        found = missing_items(None)
        assert len(found) == 1 and word in found[0], (name, found)


def test_too_few_skills_and_no_interests_are_reported(data: Path) -> None:
    fill(data, profile__profile_dot_md=PROFILE.replace("[Python, SQL, Git, Linux, C]", "[Python]"))
    assert any("at least 5 skills (you have 1)" in item for item in missing_items(None))
    thin = PROFILE.replace("interests: [robotics]\n", "")
    fill(data, profile__profile_dot_md=thin)
    assert any("interests or an About" in item for item in missing_items(None))
    fill(data, profile__profile_dot_md=thin + "I like building things.\n")
    assert not any("interests" in item for item in missing_items(None))


def test_the_skill_threshold_is_a_setting_argument(data: Path) -> None:
    fill(data)
    assert missing_items(None, min_skills=6) != []
    assert missing_items(None, min_skills=5) == []


def test_accepted_resume_facts_count_toward_skills(data: Path) -> None:
    fill(data, profile__profile_dot_md=PROFILE.replace("[Python, SQL, Git, Linux, C]", "[Python]"))
    engine = open_engine(data / "t.db", new_database_key())
    migrate(engine)
    with session_factory(engine)() as session:
        session.add(
            ProfileExtraction(
                kind="resume",
                file_sha256="a" * 64,
                prompt_version=1,
                status="accepted",
                facts={"skills": ["SQL", "Git", "Linux", "C", "python"]},
            )
        )
        session.commit()
        assert missing_items(session) == []
        pending = session.query(ProfileExtraction).one()
        pending.status = "pending"
        session.commit()
        assert missing_items(session) != []  # pending facts do not count


def test_a_broken_profile_file_is_reported_not_raised(data: Path) -> None:
    fill(data, profile__profile_dot_md="not front matter")
    assert "has a problem" in missing_items(None)[0]
    assert missing_items(None) != []


def test_the_switch_is_stored_with_its_date_and_can_be_removed(data: Path) -> None:
    assert scoring_since() is None
    set_scoring(True, date(2026, 10, 22))
    assert scoring_since() == date(2026, 10, 22)
    assert (data / "profile" / "ready.json").stat().st_mode & 0o077 == 0
    assert current_readiness(None).on
    set_scoring(False, date(2026, 10, 22))
    assert scoring_since() is None
    (data / "profile" / "ready.json").write_text("garbage")
    assert scoring_since() is None  # a damaged switch means off


def test_hub_profile_ready_refuses_until_the_checklist_passes_then_switches_on_and_off(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = InMemoryKeyStore()
    fill(data, profile__profile_dot_md=PROFILE.replace("[Python, SQL, Git, Linux, C]", "[Python]"))
    assert main(["profile", "ready"], store) == 1
    assert "at least 5 skills" in capsys.readouterr().out and scoring_since() is None
    fill(data)
    assert main(["profile", "ready"], store) == 0
    assert scoring_since() is not None and "Scoring is on" in capsys.readouterr().out
    assert main(["profile", "ready", "--off"], store) == 0
    assert scoring_since() is None
    assert main(["profile", "ready", "--bogus"], store) == 2


def test_the_page_says_scoring_starts_when_the_profile_is_ready_and_what_is_missing(
    data: Path,
) -> None:
    fill(data, inputs__resume__resume_dot_txt=None)
    engine = open_engine(data / "t.db", new_database_key())
    migrate(engine)
    with session_factory(engine)() as session:
        shortlist = gather_shortlist(session)
        assert shortlist.waiting_for is not None and "resume" in shortlist.waiting_for[0]
        html = render_shortlist(shortlist, date_now())
        assert "Scoring starts when your profile is ready" in html and "<li>a resume" in html
        set_scoring(True, date(2026, 10, 22))
        assert gather_shortlist(session).waiting_for is None
        assert "Scoring starts" not in render_shortlist(gather_shortlist(session), date_now())


def date_now():  # type: ignore[no-untyped-def]
    from careerhub.core.models import utcnow

    return utcnow()


def test_an_empty_shortlist_data_has_no_waiting_message() -> None:
    assert ShortlistData().waiting_for is None
