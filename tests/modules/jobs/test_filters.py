from __future__ import annotations

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
from careerhub.modules.jobs.filters import apply_filters, assess_filters
from careerhub.profile.models import HardConstraints


def job(
    title: str = "Software Engineer", company: str = "Acme", location: str | None = "Boston, MA"
) -> Job:
    return Job(
        company=Company(name=company, name_key=company.lower()),
        title=title,
        location=location,
        structured_fields={},
    )


def test_a_job_with_no_conflicts_passes() -> None:
    result = apply_filters(job(), None, HardConstraints())
    assert result.passed and result.reasons == [] and result.uncertain == []


def test_excluded_company_and_title_words_reject_with_reasons() -> None:
    constraints = HardConstraints(excluded_companies=[" ACME "], excluded_title_words=["senior"])
    result = apply_filters(job("Senior Engineer"), None, constraints)
    assert not result.passed and len(result.reasons) == 2
    assert "excluded list" in result.reasons[0] and "senior" in result.reasons[1]


def test_title_words_match_whole_words_only() -> None:
    constraints = HardConstraints(excluded_title_words=["intern"])
    assert apply_filters(job("Internal Tools Engineer"), None, constraints).passed
    assert not apply_filters(job("Software Intern"), None, constraints).passed


def test_locations_match_by_city_and_reject_others() -> None:
    constraints = HardConstraints(locations=["Boston, MA"], remote_ok=False)
    assert apply_filters(job(location="Boston, Massachusetts"), None, constraints).passed
    rejected = apply_filters(job(location="Austin, TX"), None, constraints)
    assert not rejected.passed and "Austin" in rejected.reasons[0]


def test_remote_jobs_pass_only_when_remote_is_ok() -> None:
    fields = JobFields(remote="remote", locations=["Denver, CO"])
    assert apply_filters(job(), fields, HardConstraints(locations=["Boston, MA"])).passed
    strict = HardConstraints(locations=["Boston, MA"], remote_ok=False)
    assert not apply_filters(job(), fields, strict).passed


def test_extracted_locations_beat_the_listed_one() -> None:
    fields = JobFields(locations=["Boston, MA", "Austin, TX"])
    constraints = HardConstraints(locations=["Austin, TX"], remote_ok=False)
    assert apply_filters(job(location="Boston, MA"), fields, constraints).passed


def test_unknown_facts_pass_and_are_noted_never_dropped() -> None:
    """The step 6 rule: filters never drop a posting on missing information."""
    constraints = HardConstraints(locations=["Boston, MA"], min_salary_usd=90000)
    result = apply_filters(job(location=None), JobFields(), constraints)
    assert result.passed and len(result.uncertain) == 2


def test_salary_below_the_minimum_rejects_but_a_high_top_end_passes() -> None:
    constraints = HardConstraints(min_salary_usd=100_000)
    low = JobFields(salary_min_usd=60_000, salary_max_usd=80_000)
    wide = JobFields(salary_min_usd=60_000, salary_max_usd=120_000)
    assert not apply_filters(job(), low, constraints).passed
    assert apply_filters(job(), wide, constraints).passed


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_results_are_saved_once_per_job_and_overwritten_on_rerun(session: Session) -> None:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Senior Engineer", "u", "Boston, MA")])
    saved = session.scalars(select(Job)).one()
    first = assess_filters(session, saved, HardConstraints(excluded_title_words=["senior"]))
    assert not first.filter_passed and first.filter_results["reasons"]
    second = assess_filters(session, saved, HardConstraints())
    assert second.id == first.id and second.filter_passed
    assert len(session.scalars(select(Assessment)).all()) == 1


# --- Work modes (CH-85) ----------------------------------------------------------------------


def mode_result(mode: str, place: str, **switches: bool):  # type: ignore[no-untyped-def]
    fields = JobFields(remote=mode, locations=[place])  # type: ignore[arg-type]
    constraints = HardConstraints(locations=["Boston, MA"], **switches)
    return apply_filters(job(location=place), fields, constraints)


@pytest.mark.parametrize("mode", ["remote", "hybrid", "onsite"])
@pytest.mark.parametrize("place", ["Boston, MA", "Denver, CO"])
def test_with_every_switch_on_only_the_location_rule_applies(mode: str, place: str) -> None:
    passed = mode_result(mode, place).passed
    assert passed is (mode == "remote" or place == "Boston, MA")


def test_remote_jobs_pass_from_anywhere_when_remote_is_on_and_fail_when_off() -> None:
    assert mode_result("remote", "Denver, CO").passed
    off = mode_result("remote", "Denver, CO", remote_ok=False)
    assert not off.passed and off.reasons == ["remote, and you turned remote off"]
    assert not mode_result("remote", "Boston, MA", remote_ok=False).passed


def test_hybrid_passes_only_in_your_locations_and_can_be_turned_off() -> None:
    assert mode_result("hybrid", "Boston, MA").passed
    assert not mode_result("hybrid", "Denver, CO").passed
    off = mode_result("hybrid", "Boston, MA", hybrid_ok=False)
    assert off.reasons == ["hybrid, and you turned hybrid off"]
    assert mode_result("hybrid", "Boston, MA", remote_ok=False, onsite_ok=False).passed


def test_onsite_passes_only_in_your_locations_and_can_be_turned_off() -> None:
    assert mode_result("onsite", "Boston, MA").passed
    assert not mode_result("onsite", "Denver, CO").passed
    off = mode_result("onsite", "Boston, MA", onsite_ok=False)
    assert off.reasons == ["onsite, and you turned onsite off"]


def test_one_clear_reason_when_a_mode_is_off_not_two() -> None:
    result = mode_result("hybrid", "Denver, CO", hybrid_ok=False)
    assert result.reasons == ["hybrid, and you turned hybrid off"]


def test_an_unknown_mode_is_never_filtered_on_mode_only_noted_when_a_switch_is_off() -> None:
    quiet = apply_filters(job(), JobFields(), HardConstraints(locations=["Boston, MA"]))
    assert quiet.passed and quiet.uncertain == []
    noted = apply_filters(
        job(), JobFields(), HardConstraints(locations=["Boston, MA"], hybrid_ok=False)
    )
    assert noted.passed and "remote, hybrid, or onsite" in noted.uncertain[0]
    far = apply_filters(
        job(location="Denver, CO"),
        JobFields(),
        HardConstraints(locations=["Boston, MA"], onsite_ok=False),
    )
    assert (
        not far.passed and "outside your locations" in far.reasons[0]
    )  # the location rule still applies


def test_no_extracted_facts_means_unknown_mode() -> None:
    result = apply_filters(job(), None, HardConstraints(onsite_ok=False))
    assert result.passed and "remote, hybrid, or onsite" in result.uncertain[0]


def test_with_no_locations_set_hybrid_and_onsite_pass_anywhere() -> None:
    assert apply_filters(job(), JobFields(remote="hybrid"), HardConstraints()).passed
    assert apply_filters(job(), JobFields(remote="onsite"), HardConstraints()).passed
