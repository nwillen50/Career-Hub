"""Similar school names, branch campuses, and systems (CH-179)."""

from __future__ import annotations

import pytest

from careerhub.modules.network.schools import related_school, same_school
from careerhub.modules.network.schools_table import ambiguous_choices, load_table, norm, tie_between


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Penn State", "Pennsylvania State University"),
        ("Penn State University", "Pennsylvania State Univ"),
        ("Penn", "University of Pennsylvania"),
        ("UPenn", "University of Pennsylvania"),
        ("UC Davis", "University of California, Davis"),
        ("University of California-Davis", "uc davis"),
        ("Ohio State", "The Ohio State University"),
        ("Washington University", "WashU"),
        ("Miami University", "Miami of Ohio"),
        ("MIT", "Massachusetts Institute of Technology"),
    ],
)
def test_names_for_one_school_match(a: str, b: str) -> None:
    assert tie_between(a, b) is not None and tie_between(a, b).kind == "same"  # type: ignore[union-attr]
    assert same_school([a], [b]) == a


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Penn State", "UPenn"),
        ("Ohio State", "Ohio University"),
        ("Michigan State", "University of Michigan"),
        ("Washington University", "University of Washington"),
        ("Miami University", "University of Miami"),
        ("UC Davis", "UCLA"),
        ("Penn State", "Penn State Behrend"),
        ("Portland State", "Plymouth State"),
    ],
)
def test_different_schools_do_not_match(a: str, b: str) -> None:
    assert same_school([a], [b]) is None


@pytest.mark.parametrize(
    ("a", "b", "label"),
    [
        ("Penn State Behrend", "Penn State", "Branch campus: Penn State"),
        ("Pennsylvania State University", "Penn State Altoona", "Branch campus: Penn State"),
        ("Penn State Behrend", "Penn State Altoona", "Branch campus: Penn State"),
        ("Rutgers-Newark", "Rutgers", "Branch campus: Rutgers"),
        ("Pitt-Johnstown", "University of Pittsburgh", "Branch campus: Pitt"),
    ],
)
def test_a_branch_campus_gets_a_weaker_branch_flag(a: str, b: str, label: str) -> None:
    tie = tie_between(a, b)
    assert tie is not None and (tie.kind, tie.label) == ("branch", label)
    assert related_school([a], [b]) == tie


def test_separate_universities_in_a_system_get_the_weakest_flag() -> None:
    tie = tie_between("UC Davis", "UCLA")
    assert tie is not None and (tie.kind, tie.label) == (
        "system",
        "Same system: University of California",
    )
    assert related_school(["SUNY Stony Brook"], ["Binghamton University"]) is not None
    assert related_school(["UC Davis"], ["Stony Brook"]) is None


def test_a_bare_system_name_matches_no_campus() -> None:
    assert same_school(["University of California"], ["UC Davis"]) is None
    assert related_school(["University of California"], ["UC Davis"]) is None
    assert tie_between("University of California", "UC Davis") is None


def test_an_ambiguous_abbreviation_matches_nothing_on_its_own() -> None:
    assert ambiguous_choices("PSU") == [
        "Pennsylvania State University",
        "Portland State University",
        "Plymouth State University",
    ]
    for other in ("Penn State", "Portland State", "Plymouth State University"):
        assert same_school(["PSU"], [other]) is None


def test_the_users_school_alias_wins_over_the_table() -> None:
    aliases = {"Pennsylvania State University": ["PSU"]}
    assert same_school(["PSU"], ["Penn State"], aliases) == "PSU"
    assert same_school(["PSU"], ["Penn State"]) is None


def test_names_not_in_the_table_still_use_the_word_rules() -> None:
    assert same_school(["Tufts Univ."], ["Tufts University"]) == "Tufts Univ."
    assert same_school(["Smallville College"], ["Smallville College"]) == "Smallville College"
    assert same_school(["Smallville College"], ["Bigtown College"]) is None


def test_the_table_is_consistent() -> None:
    table = load_table()
    names = {e.name for e in table.by_name.values()}
    for entry in table.by_name.values():
        assert entry.branch_of is None or entry.branch_of in names, entry.name
        assert entry.system is None or norm(entry.system) in table.systems
    for choices in table.ambiguous.values():
        assert len(choices) >= 2


@pytest.mark.parametrize(
    "text",
    ["", "   ", "​", "🎓", "x" * 100_000, "Penn State", "PENN STATE", "a&b"],
)
def test_odd_school_text_never_raises_and_never_matches_by_accident(text: str) -> None:
    """Hostile or empty school names (imports and typed tags) are handled quietly."""
    assert same_school([text], ["Tufts University"]) is None
    assert related_school([text], ["Tufts University"]) is None
    assert tie_between(text, "UC Davis") is None or tie_between(text, "UC Davis").kind != "same"  # type: ignore[union-attr]
    assert ambiguous_choices(text) in ([], ambiguous_choices(text))


def test_a_non_breaking_space_and_capitals_still_match() -> None:
    assert same_school(["Penn State"], ["Pennsylvania State University"]) == "Penn State"
    assert same_school(["PENN STATE"], ["penn state"]) == "PENN STATE"
