"""Tests for the eligibility filters and lenient matching (CH-113). All data is made up."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Company, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.jobs.extract import JobFields
from careerhub.modules.jobs.filters import apply_filters
from careerhub.modules.jobs.scoring import ranked_jobs
from careerhub.profile.eligibility import Eligibility, degree_level, eligibility_from
from careerhub.profile.extract import EducationFact
from careerhub.profile.models import Education, HardConstraints, Preferences, ProfileDoc

ME = Eligibility(highest_degree="bachelor", graduation=date(2027, 5, 1))


def job(title: str = "Engineer") -> Job:
    return Job(
        company=Company(name="Acme", name_key="acme"),
        title=title,
        location="Boston, MA",
        structured_fields={},
    )


def check(fields: JobFields, me: Eligibility = ME):  # type: ignore[no-untyped-def]
    return apply_filters(job(), fields, HardConstraints(), me)


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("BS CS", "bachelor"),
        ("B.S. Physics", "bachelor"),
        ("Bachelor of Arts", "bachelor"),
        ("M.S. Physics", "master"),
        ("MBA", "master"),
        ("PhD, Optics", "phd"),
        ("Doctor of Philosophy", "phd"),
        ("AAS Nursing", "associate"),
        ("Certificate", None),
        ("", None),
    ],
)
def test_degree_levels_are_read_from_free_text(text: str, level: str | None) -> None:
    assert degree_level(text) == level


def test_eligibility_uses_the_highest_degree_and_its_graduation_date() -> None:
    doc = ProfileDoc(
        name="J",
        education=[
            Education(school="A", degree="BS CS", graduation=date(2025, 5, 1)),
            Education(school="B", degree="MS Physics", graduation=date(2027, 5, 1)),
        ],
    )
    prefs = Preferences(work_authorization="us_citizen")
    assert eligibility_from(doc, prefs) == Eligibility("master", date(2027, 5, 1), "us_citizen")
    assert (
        eligibility_from(doc, None, [EducationFact(school="S", degree="PhD")]).highest_degree
        == "phd"
    )
    assert eligibility_from(ProfileDoc(name="J"), None) == Eligibility()


def test_a_required_degree_above_yours_is_filtered_with_a_reason() -> None:
    result = check(JobFields(degree_required="master"))
    assert (
        not result.passed
        and "requires a master degree; your highest is bachelor" in result.reasons[0]
    )


def test_an_equivalent_experience_clause_makes_it_uncertain_not_filtered() -> None:
    result = check(JobFields(degree_required="master", equivalent_experience_ok="yes"))
    assert result.passed and "equivalent experience" in result.uncertain[0]


@pytest.mark.parametrize("required", ["none", "associate", "bachelor", "unknown"])
def test_degrees_at_or_below_yours_and_unknown_pass_quietly(required: str) -> None:
    result = check(JobFields(degree_required=required))  # type: ignore[arg-type]
    assert result.passed and result.uncertain == []


def test_an_unknown_degree_level_of_yours_never_filters() -> None:
    result = check(JobFields(degree_required="phd"), Eligibility())
    assert result.passed and "isn't known" in result.uncertain[0]


def test_a_graduation_window_that_excludes_you_is_filtered() -> None:
    late = JobFields(graduation_earliest=date(2028, 1, 1), graduation_latest=date(2028, 12, 31))
    early = JobFields(graduation_earliest=date(2025, 1, 1), graduation_latest=date(2026, 12, 31))
    assert "you graduate 2027-05-01" in check(late).reasons[0]
    assert not check(early).passed
    assert check(
        JobFields(graduation_earliest=date(2027, 1, 1), graduation_latest=date(2027, 12, 31))
    ).passed
    assert check(JobFields(graduation_latest=date(2027, 6, 1))).passed  # a one-sided window


def test_a_window_with_an_unknown_graduation_date_is_uncertain() -> None:
    fields = JobFields(graduation_latest=date(2026, 12, 31))
    result = check(fields, Eligibility(highest_degree="bachelor"))
    assert result.passed and "graduation date isn't known" in result.uncertain[0]


def test_citizenship_is_uncertain_until_you_state_your_authorization() -> None:
    fields = JobFields(work_authorization="us_citizen_required")
    unset = check(fields)
    assert unset.passed and "work_authorization" in unset.uncertain[0]
    assert check(fields, Eligibility("bachelor", None, "us_citizen")).passed
    blocked = check(fields, Eligibility("bachelor", None, "needs_sponsorship"))
    assert not blocked.passed and "needs_sponsorship" in blocked.reasons[0]


def test_a_clearance_is_never_a_reason_to_filter() -> None:
    result = check(JobFields(work_authorization="clearance_required"))
    assert result.passed and result.uncertain == ["requires a security clearance"]


@pytest.mark.parametrize("level", ["internship", "new_grad", "entry", "mid", "senior"])
def test_job_level_is_never_a_filter(level: str) -> None:
    assert check(JobFields(level=level)).passed  # type: ignore[arg-type]


def test_degree_field_is_never_a_filter() -> None:
    fields = JobFields(degree_required="bachelor", degree_fields=["Electrical Engineering"])
    assert check(fields).passed


def test_no_extracted_facts_means_no_eligibility_filtering() -> None:
    assert apply_filters(job(), None, HardConstraints(), ME).passed


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def seed(session: Session) -> None:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    items = [
        NormalizedJob("Acme", "1", "Summer Intern", "u1", "Boston"),
        NormalizedJob("Acme", "2", "Engineer", "u2", "Boston"),
        NormalizedJob("Acme", "3", "Analyst", "u3", "Boston"),
    ]
    store_jobs(session, source, items)
    levels = {"1": "internship", "2": "entry", "3": "new_grad"}
    scores = {"1": 86.0, "2": 80.0, "3": 78.0}
    for row in session.scalars(select(Job)):
        row.structured_fields = {"fields": {"level": levels[row.external_id]}}
        session.add(
            Assessment(job_id=row.id, filter_passed=True, fit_score=scores[row.external_id])
        )
    session.commit()


def test_the_internship_penalty_changes_the_order_but_never_the_stored_score(
    session: Session,
) -> None:
    seed(session)
    assert [j.title for j, _ in ranked_jobs(session)] == ["Summer Intern", "Engineer", "Analyst"]
    penalised = ranked_jobs(session, internship_penalty=1.0)  # 10 points of 100
    assert [j.title for j, _ in penalised] == ["Engineer", "Analyst", "Summer Intern"]
    scores = {j.title: a.fit_score for j, a in penalised}
    assert scores["Summer Intern"] == 86.0  # the stored score is untouched
    assert [j.title for j, _ in ranked_jobs(session, 1, internship_penalty=1.0)] == ["Engineer"]
    small = ranked_jobs(session, internship_penalty=0.5)  # 5 points: 81 still beats 80
    assert [j.title for j, _ in small][0] == "Summer Intern"


def test_the_new_preference_keys_load_and_are_validated() -> None:
    prefs = Preferences.model_validate(
        {"also_qualified_fields": ["Electrical Engineering"], "internship_rank_penalty": 1}
    )
    assert prefs.also_qualified_fields == ["Electrical Engineering"]
    assert Preferences().internship_rank_penalty == 0 and Preferences().work_authorization is None
    for bad in (
        {"internship_rank_penalty": -1},
        {"internship_rank_penalty": 11},
        {"work_authorization": "maybe"},
    ):
        with pytest.raises(ValueError):
            Preferences.model_validate(bad)
