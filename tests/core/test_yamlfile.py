"""Editing a user's YAML keeps their comments and layout (CH-180)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.core.yamlfile import edit_yaml_text
from careerhub.modules.proposals import PreferenceChange, ProposalError, accept, propose
from careerhub.profile.files import preferences_path

SAMPLE = """# My job search settings (hello from me)

hard_constraints:
  # where I would live
  locations: ['Boston, MA', "New York, NY"]   # no West Coast
  # excluded_title_words: [manager]   <- tried this once
  excluded_title_words:
    - intern    # not an internship
    - sales

  remote_ok: true

weekly_time_budget_hours: 6   # about an hour a day

# plan settings
plan:
  apply_minutes: 90
"""


def edited(change: dict[str, object]) -> str:
    data = yaml.safe_load(SAMPLE)
    for dotted, value in change.items():
        node = data
        *parents, last = dotted.split(".")
        for part in parents:
            node = node[part]
        node[last] = value
    return edit_yaml_text(SAMPLE, data)


def only_changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    old, new = before.splitlines(), after.splitlines()
    return [x for x in old if x not in new], [x for x in new if x not in old]


def test_only_the_edited_line_differs_and_every_comment_survives() -> None:
    text = edited({"weekly_time_budget_hours": 8})
    removed, added = only_changed_lines(SAMPLE, text)
    assert removed == ["weekly_time_budget_hours: 6   # about an hour a day"]
    assert added == ["weekly_time_budget_hours: 8   # about an hour a day"]
    assert yaml.safe_load(text)["weekly_time_budget_hours"] == 8


def test_comments_blank_lines_quoting_order_and_commented_out_lines_survive() -> None:
    text = edited({"hard_constraints.remote_ok": False})
    for kept in (
        "# My job search settings (hello from me)",
        "  # where I would live",
        "  # excluded_title_words: [manager]   <- tried this once",
        "  locations: ['Boston, MA', \"New York, NY\"]   # no West Coast",
        "    - intern    # not an internship",
        "# plan settings",
    ):
        assert kept in text.splitlines(), kept
    assert text.count("\n\n") == SAMPLE.count("\n\n")  # blank lines
    keys = [line.split(":")[0] for line in text.splitlines() if line and line[0].isalpha()]
    assert keys == ["hard_constraints", "weekly_time_budget_hours", "plan"]


def test_adding_and_removing_list_items_keeps_the_rest() -> None:
    data = yaml.safe_load(SAMPLE)
    data["hard_constraints"]["excluded_title_words"].append("recruiter")
    added = edit_yaml_text(SAMPLE, data)
    assert "    - intern    # not an internship" in added and "- recruiter" in added
    assert yaml.safe_load(added) == data
    data["hard_constraints"]["excluded_title_words"].remove("sales")
    removed = edit_yaml_text(SAMPLE, data)
    assert "- sales" not in removed and "# not an internship" in removed
    assert yaml.safe_load(removed) == data


def test_a_new_key_and_a_removed_key() -> None:
    data = yaml.safe_load(SAMPLE)
    data["timeline"] = {"earliest_start": "2027-06-01"}
    del data["plan"]
    text = edit_yaml_text(SAMPLE, data)
    assert "timeline:" in text and "plan:" not in text and "# My job search" in text
    assert yaml.safe_load(text) == data


def test_a_new_file_and_unreadable_originals_fall_back_to_a_plain_dump() -> None:
    assert yaml.safe_load(edit_yaml_text(None, {"a": 1})) == {"a": 1}
    assert yaml.safe_load(edit_yaml_text("  \n", {"a": 1})) == {"a": 1}
    assert yaml.safe_load(edit_yaml_text("a: [unclosed", {"a": 1})) == {"a": 1}
    assert yaml.safe_load(edit_yaml_text("- just\n- a list\n", {"a": 1})) == {"a": 1}


def test_unsafe_yaml_in_the_original_is_never_run() -> None:
    text = edit_yaml_text("a: !!python/object/apply:os.system ['echo hi']\n", {"a": 1})
    assert yaml.safe_load(text) == {"a": 1}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "profile").mkdir(parents=True)
    preferences_path().write_text(SAMPLE, encoding="utf-8")
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_accepting_a_proposal_keeps_the_users_comments(session: Session) -> None:
    proposal = propose(
        session, PreferenceChange(key="excluded_title_words", action="add", value="recruiter")
    )
    accept(session, proposal.id)
    text = preferences_path().read_text(encoding="utf-8")
    removed, added = only_changed_lines(SAMPLE, text)
    assert removed == [] and any("recruiter" in line for line in added)
    assert "# excluded_title_words: [manager]   <- tried this once" in text
    assert "    - intern    # not an internship" in text


def test_a_broken_file_is_still_refused(session: Session) -> None:
    proposal = propose(
        session, PreferenceChange(key="excluded_title_words", action="add", value="recruiter")
    )
    preferences_path().write_text("hard_constraints: [unclosed", encoding="utf-8")
    with pytest.raises(ProposalError):
        accept(session, proposal.id)


def test_removing_or_inserting_a_list_item_keeps_every_comment_on_its_own_item() -> None:
    """Review finding: comments used to stay at their index while the values moved."""
    text = "locations:\n  - Boston  # home, keep\n  - Remote  # flexible\n  - Austin  # maybe\n"
    data = yaml.safe_load(text)
    data["locations"].remove("Boston")
    removed = edit_yaml_text(text, data)
    assert "- Remote  # flexible" in removed and "- Austin  # maybe" in removed
    assert "Boston" not in removed and "home, keep" not in removed
    data = yaml.safe_load(text)
    data["locations"].insert(0, "Chicago")
    inserted = edit_yaml_text(text, data)
    assert "- Boston  # home, keep" in inserted and "- Remote  # flexible" in inserted
    assert yaml.safe_load(inserted) == data
    data["locations"][2] = "Denver"  # replace one in the middle
    replaced = edit_yaml_text(text, data)
    assert "- Boston  # home, keep" in replaced and "- Austin  # maybe" in replaced
    assert yaml.safe_load(replaced) == data


def test_a_changed_item_in_a_list_of_mappings_keeps_its_comment() -> None:
    text = "criteria:\n  # first\n  - name: a\n    weight: 1  # light\n  - name: b\n    weight: 2\n"
    data = yaml.safe_load(text)
    data["criteria"][1]["weight"] = 5
    out = edit_yaml_text(text, data)
    assert "# first" in out and "weight: 1  # light" in out and "weight: 5" in out
