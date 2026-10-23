"""Only meaningful profile changes trigger re-scoring (CH-115)."""

from __future__ import annotations

from pathlib import Path

import pytest

from careerhub.profile.changes import (
    build_snapshot,
    check,
    edit_distance,
    force_rescore,
    meaningful_changes,
)

WORDS = " ".join(f"word{i}" for i in range(100))


def snap(
    skills: list[str] | None = None,
    about: str = WORDS,
    facts: str = "",
    prefs: list[str] | None = None,
) -> dict[str, list[str]]:
    return build_snapshot(
        education=["BS CS Example University"],
        skills=skills if skills is not None else ["Python", "SQL", "Machine Learning"],
        interests=["robotics"],
        preferences=prefs or ["Locations: Boston; work modes: remote ok"],
        about=about,
        facts=facts,
    )


def changes(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[str]:
    return meaningful_changes(old, new, typo_distance=2, free_percent=5)


def test_edit_distance() -> None:
    assert edit_distance("python", "pyton") == 1
    assert edit_distance("sql", "nosql") == 2
    assert edit_distance("", "abc") == 3


def test_formatting_and_order_are_not_changes() -> None:
    assert changes(snap(), snap(skills=["machine  learning!", "SQL", "python"])) == []


def test_a_typo_fix_does_not_count_but_a_new_or_removed_skill_does() -> None:
    assert changes(snap(), snap(skills=["Pyton", "SQL", "Machine Learning"])) == []
    assert changes(snap(), snap(skills=["Python", "SQL", "Machine Learning", "Rust"])) == [
        "skills: 1 added"
    ]
    assert changes(snap(), snap(skills=["Python", "SQL"])) == ["skills: 1 removed"]


def test_a_change_of_more_than_the_typo_distance_is_a_different_item() -> None:
    reasons = changes(snap(), snap(skills=["Python", "SQL", "Deep Learning"]))
    assert reasons == ["skills: 1 added and 1 removed"]


def test_a_changed_location_or_date_counts() -> None:
    new = snap(prefs=["Locations: Seattle; work modes: remote ok"])
    assert changes(snap(), new) and "preferences" in changes(snap(), new)[0]


def test_one_word_in_a_hundred_is_not_enough_but_small_edits_add_up() -> None:
    one = WORDS.replace("word5 ", "other ")
    assert changes(snap(), snap(about=one)) == []
    six = one
    for i in (10, 20, 30, 40, 50):
        six = six.replace(f"word{i} ", f"new{i} ")
    reasons = changes(snap(), snap(about=six))
    assert reasons and reasons[0].startswith("About: about 6%")


def test_facts_text_counts_as_free_text() -> None:
    assert changes(snap(facts=""), snap(facts="worked on a robot arm")) == [
        "accepted resume facts: about 100% of the words changed"
    ]


def test_first_run_stores_a_snapshot_and_later_runs_keep_the_epoch(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    first = check(snap(), 2, 5, path=path)
    assert first.first_run and first.epoch == 0 and path.exists()
    again = check(snap(skills=["Pyton", "SQL", "Machine Learning"]), 2, 5, path=path)
    assert not again.changed and again.epoch == 0


def test_a_meaningful_change_raises_the_epoch_once(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    check(snap(), 2, 5, path=path)
    new = snap(skills=["Python", "SQL", "Machine Learning", "Rust"])
    assert check(new, 2, 5, path=path).epoch == 1
    assert check(new, 2, 5, path=path).epoch == 1  # the snapshot moved forward
    assert not check(new, 2, 5, path=path).changed


def test_checking_without_saving_reports_but_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    check(snap(), 2, 5, path=path)
    new = snap(skills=["Python", "SQL", "Machine Learning", "Rust"])
    preview = check(new, 2, 5, save=False, path=path)
    assert preview.reasons == ["skills: 1 added"] and preview.epoch == 1
    assert check(snap(), 2, 5, path=path).epoch == 0  # still the old snapshot, still epoch 0


def test_force_rescore_raises_the_epoch(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    assert force_rescore(path) == 0  # nothing scored yet
    check(snap(), 2, 5, path=path)
    assert force_rescore(path) == 1
    assert check(snap(), 2, 5, path=path).epoch == 1


def test_the_snapshot_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    check(snap(), 2, 5, path=path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_settings_have_the_documented_defaults() -> None:
    from careerhub.core.settings import Settings

    settings = Settings()
    assert (settings.score_typo_distance, settings.score_free_text_change_percent) == (2, 5.0)
    with pytest.raises(ValueError):
        Settings(score_free_text_change_percent=101)
