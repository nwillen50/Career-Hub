from __future__ import annotations

from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.profile import ProfileFileError
from careerhub.profile.inputs import find_resume, find_transcript
from careerhub.profile.profile import format_profile, load_profile
from careerhub.profile.rubric import load_rubric, starter_rubric

RUBRIC = """
version: 2
criteria:
  - {name: field, weight: 3, description: Right field}
  - {name: level, weight: 1.5, description: Right level}
"""


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    return tmp_path


def put(base: Path, relative: str, text: str = "x") -> Path:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_rubric_loads_and_validates(tmp_path: Path) -> None:
    rubric = load_rubric(put(tmp_path, "rubric.yaml", RUBRIC))
    assert rubric is not None and rubric.version == 2
    assert [c.name for c in rubric.criteria] == ["field", "level"]
    assert load_rubric(tmp_path / "none.yaml") is None


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\ncriteria: []\n",
        "version: 1\ncriteria:\n  - {name: a, weight: 0, description: d}\n",
        "version: 1\ncriteria:\n  - {name: a, weight: 1, description: d}\n"
        "  - {name: A, weight: 2, description: e}\n",
    ],
)
def test_bad_rubrics_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ProfileFileError):
        load_rubric(put(tmp_path, "rubric.yaml", text))


def test_starter_rubric_is_valid() -> None:
    assert len(starter_rubric().criteria) == 6
    assert {c.name: c.weight for c in starter_rubric().criteria}["pay"] == 2


def test_resume_main_file_and_support_files(tmp_path: Path) -> None:
    for name in ("resume.tex", "other.tex", "x.cls", "y.sty", "notes.txt"):
        put(tmp_path, name, "\\documentclass{x}" if name == "resume.tex" else "x")
    resume, why = find_resume(tmp_path)
    assert why is None and resume is not None and resume.main.name == "resume.tex"
    assert [p.name for p in resume.support] == ["x.cls", "y.sty"]
    assert resume.read().format == "tex"


def test_resume_problems_are_reported(tmp_path: Path) -> None:
    assert find_resume(tmp_path / "missing")[1] is not None
    put(tmp_path, "a.tex")
    put(tmp_path, "b.tex")
    resume, why = find_resume(tmp_path)
    assert resume is None and why is not None and "main.tex" in why


def test_transcript(tmp_path: Path) -> None:
    assert find_transcript(tmp_path)[0] is None
    put(tmp_path, "t.pdf")
    assert find_transcript(tmp_path)[0] == tmp_path / "t.pdf"
    put(tmp_path, "u.docx")
    assert "one" in (find_transcript(tmp_path)[1] or "")


def test_an_empty_data_folder_lists_every_missing_input(data: Path) -> None:
    profile = load_profile()
    assert profile.doc is None and len(profile.problems) == 5
    assert "Resume: not found" in format_profile(profile)


def test_a_complete_profile_prints_without_resume_text(data: Path) -> None:
    put(data, "profile/profile.md", "---\nname: Jane Sample\nskills: [Python]\n---\n")
    put(data, "profile/preferences.yaml", "weekly_time_budget_hours: 4\n")
    put(data, "profile/rubric.yaml", RUBRIC)
    put(data, "inputs/resume/resume.tex", "SECRET RESUME LINE")
    put(data, "inputs/transcript/t.txt", "CS101 A fall term courses and grades")
    profile = load_profile()
    text = format_profile(profile)
    assert profile.problems == []
    assert "Jane Sample" in text and "Rubric v2" in text and "resume.tex" in text
    assert "SECRET RESUME LINE" not in text


def test_hub_profile_show(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(data, "profile/profile.md", "---\nname: Jane Sample\n---\n")
    assert main(["profile", "show"], store=InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    assert "Name: Jane Sample" in out and "connections imported: 0" in out


def test_hub_reports_a_broken_file_and_bad_usage(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put(data, "profile/preferences.yaml", "bogus: 1\n")
    assert main(["profile", "show"], store=InMemoryKeyStore()) == 1
    assert "bogus" in capsys.readouterr().out
    assert main(["nope"]) == 2
