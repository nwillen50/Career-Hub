from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from careerhub.profile import (
    ProfileFileError,
    load_preferences,
    load_profile_doc,
    preferences_path,
    profile_doc_path,
)
from careerhub.profile.models import HardConstraints

PROFILE = """---
name: Jane Sample
education:
  - school: Example University
    degree: BS Computer Science
    graduation: 2027-05-01
skills: [Python, SQL]
interests: [machine learning]
---

I like small teams.
Second line.
"""

PREFERENCES = """
hard_constraints:
  locations: ["Boston, MA"]
  remote_ok: false
  min_salary_usd: 90000
  excluded_companies: [Acme]
  excluded_title_words: [senior]
timeline:
  earliest_start: 2027-06-01
weekly_time_budget_hours: 6
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_profile_front_matter_and_body(tmp_path: Path) -> None:
    doc = load_profile_doc(write(tmp_path, "profile.md", PROFILE))
    assert doc is not None
    assert doc.name == "Jane Sample" and doc.skills == ["Python", "SQL"]
    assert doc.education[0].graduation == date(2027, 5, 1)
    assert doc.about == "I like small teams.\nSecond line."


def test_preferences(tmp_path: Path) -> None:
    prefs = load_preferences(write(tmp_path, "preferences.yaml", PREFERENCES))
    assert prefs is not None
    assert prefs.hard_constraints.locations == ["Boston, MA"]
    assert prefs.hard_constraints.remote_ok is False
    assert prefs.timeline.earliest_start == date(2027, 6, 1)
    assert prefs.weekly_time_budget_hours == 6


def test_an_empty_preferences_file_means_defaults(tmp_path: Path) -> None:
    prefs = load_preferences(write(tmp_path, "preferences.yaml", ""))
    assert prefs is not None and prefs.hard_constraints.remote_ok is True
    assert prefs.weekly_time_budget_hours == 5.0


def test_missing_files_are_none_not_errors(tmp_path: Path) -> None:
    assert load_profile_doc(tmp_path / "nope.md") is None
    assert load_preferences(tmp_path / "nope.yaml") is None


def test_default_locations_are_in_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert profile_doc_path() == tmp_path / "profile" / "profile.md"
    assert preferences_path() == tmp_path / "profile" / "preferences.yaml"


def test_profile_without_front_matter_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProfileFileError, match="three dashes"):
        load_profile_doc(write(tmp_path, "profile.md", "Just text about Jane."))


def test_unknown_field_is_an_error_naming_the_field_not_the_value(tmp_path: Path) -> None:
    text = PREFERENCES + "locatoins: [secret-city]\n"
    with pytest.raises(ProfileFileError) as raised:
        load_preferences(write(tmp_path, "preferences.yaml", text))
    assert "locatoins" in str(raised.value) and "secret-city" not in str(raised.value)


def test_bad_value_is_reported_without_the_value(tmp_path: Path) -> None:
    text = "hard_constraints:\n  min_salary_usd: -123456\n"
    with pytest.raises(ProfileFileError) as raised:
        load_preferences(write(tmp_path, "preferences.yaml", text))
    assert "min_salary_usd" in str(raised.value) and "123456" not in str(raised.value)


def test_invalid_yaml_reports_the_line_only(tmp_path: Path) -> None:
    with pytest.raises(ProfileFileError, match=r"not valid YAML \(line \d+\)"):
        load_preferences(write(tmp_path, "preferences.yaml", "a: [unclosed\nb: 1\n"))


def test_a_list_instead_of_fields_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProfileFileError, match="named fields"):
        load_preferences(write(tmp_path, "preferences.yaml", "- a\n- b\n"))


def test_profile_requires_a_name(tmp_path: Path) -> None:
    with pytest.raises(ProfileFileError, match="name"):
        load_profile_doc(write(tmp_path, "profile.md", "---\nskills: [x]\n---\n"))


def test_hybrid_and_onsite_switches_default_to_on_and_load_from_the_file(tmp_path: Path) -> None:
    assert HardConstraints().hybrid_ok is True and HardConstraints().onsite_ok is True
    path = tmp_path / "preferences.yaml"
    path.write_text("hard_constraints:\n  hybrid_ok: false\n  onsite_ok: false\n", encoding="utf-8")
    prefs = load_preferences(path)
    assert prefs is not None
    assert (prefs.hard_constraints.hybrid_ok, prefs.hard_constraints.onsite_ok) == (False, False)
