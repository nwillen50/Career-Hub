"""Work-visa flags and the hide_no_visa_abroad filter (CH-122). Saved samples, no network."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from careerhub.core.models import Company, Job
from careerhub.modules.jobs.extract import JobFields
from careerhub.modules.jobs.filters import apply_filters
from careerhub.modules.jobs.visa import visa_flag
from careerhub.profile.eligibility import Eligibility
from careerhub.profile.models import HardConstraints, Preferences
from careerhub.web.shortlist import _render_visa


def fields(
    visa: str = "unknown", auth: str = "unknown", places: list[str] | None = None
) -> JobFields:
    return JobFields.model_validate(
        {"visa_sponsorship": visa, "work_authorization": auth, "locations": places or []}
    )


def test_a_home_country_job_has_no_flag() -> None:
    assert visa_flag(fields(places=["Boston, MA"]), "Boston, MA", ["US"]) is None
    assert visa_flag(None, "Boston, MA", ["US"]) is None


@pytest.mark.parametrize(
    ("visa", "text", "refuses"),
    [
        ("yes", "Needs a work visa for Germany: sponsors visas", False),
        ("no", "Needs a work visa for Germany: won't sponsor", True),
        ("unknown", "Needs a work visa for Germany: sponsorship not stated", False),
    ],
)
def test_a_job_abroad_gets_a_flag_in_words(visa: str, text: str, refuses: bool) -> None:
    flag = visa_flag(fields(visa, places=["Berlin, Germany"]), None, ["US"])
    assert flag is not None and flag.text == text and flag.posting_refuses is refuses


def test_a_required_local_right_to_work_is_stated_and_refuses() -> None:
    flag = visa_flag(
        fields(auth="local_right_to_work_required", places=["Munich, DE"]), None, ["US"]
    )
    assert flag is not None and "requires an existing right to work there" in flag.text
    assert flag.posting_refuses


def test_remote_open_to_my_country_has_no_flag_but_remote_elsewhere_does() -> None:
    assert visa_flag(fields(places=["Remote - US"]), None, ["US"]) is None
    assert visa_flag(fields(places=["Remote"]), None, ["US"]) is None  # no country: never guessed
    assert visa_flag(fields(places=["Remote (Germany)"]), None, ["US"]) is not None


def test_one_place_where_i_can_work_removes_the_flag() -> None:
    assert visa_flag(fields(places=["Berlin, Germany", "Boston, MA"]), None, ["US"]) is None
    flag = visa_flag(fields(places=["Berlin, Germany", "Paris, France"]), None, ["US"])
    assert flag is not None and "Germany or France" in flag.text


def test_with_no_work_rights_set_every_country_gets_the_flag() -> None:
    flag = visa_flag(fields(places=["Boston, MA"]), None, [])
    assert flag is not None and "United States" in flag.text
    assert visa_flag(fields(places=["Boston, MA"]), None, None) is not None


def test_an_unreadable_place_gets_no_flag() -> None:
    assert visa_flag(fields(places=["Building 7, Campus Drive"]), None, ["US"]) is None


def job() -> Job:
    return Job(title="Engineer", company=Company(name="Acme"), location="Berlin, Germany")


def run(visa: str, auth: str, hide: bool, rights: tuple[str, ...] | None = ("US",)) -> list[str]:
    result = apply_filters(
        job(),
        fields(visa, auth, ["Berlin, Germany"]),
        HardConstraints(hide_no_visa_abroad=hide),
        Eligibility(work_rights=rights),
    )
    return result.reasons


def test_the_filter_hides_only_what_the_posting_refuses() -> None:
    assert run("no", "unknown", hide=True) and "won't sponsor" in run("no", "unknown", True)[0]
    assert run("unknown", "local_right_to_work_required", hide=True)
    assert run("unknown", "unknown", hide=True) == []  # not stated is never rejected
    assert run("yes", "unknown", hide=True) == []
    assert run("no", "unknown", hide=False) == []  # off by default


def test_the_filter_never_hides_a_job_in_my_own_country() -> None:
    result = apply_filters(
        Job(title="Engineer", company=Company(name="Acme"), location="Boston, MA"),
        fields("no", places=["Boston, MA"]),
        HardConstraints(hide_no_visa_abroad=True),
        Eligibility(work_rights=("US",)),
    )
    assert result.reasons == []


def test_existing_us_citizenship_rules_are_unchanged() -> None:
    result = apply_filters(
        Job(title="Engineer", company=Company(name="Acme"), location="Boston, MA"),
        fields(auth="us_citizen_required", places=["Boston, MA"]),
        HardConstraints(),
        Eligibility(work_authorization="needs_sponsorship"),
    )
    assert result.reasons == ["requires US citizenship; your setting is needs_sponsorship"]


def test_work_rights_take_two_letter_codes_in_capitals() -> None:
    assert Preferences(work_rights=["us", " ca "]).work_rights == ["US", "CA"]
    with pytest.raises(ValidationError):
        Preferences(work_rights=["USA"])


def test_the_card_note_is_text_and_escaped() -> None:
    assert _render_visa(None) == ""
    assert "Work visa:" in _render_visa("Needs a work visa for Germany: sponsors visas")
    assert "<script>" not in _render_visa("<script>")
