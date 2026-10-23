"""Remembering the column mapping per alumni file name (CH-116)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.modules.network.mappings import (
    SavedMapping,
    forget_mapping,
    list_mappings,
    load_mapping,
    mappings_path,
    save_mapping,
)

ODD = "Full Name,Current Employer,Group\nAda Sample,Acme Corp,Example University\n"
NEWER = "Full Name,Current Employer,Group\nBen Example,Globex,Example University\n"
CHANGED = "Full Name,Employer Now,Group\nCy Other,Initech,Example University\n"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path


Run = Callable[..., int]


@pytest.fixture
def run(data: Path) -> Run:
    """Run `hub` with one key store per test (a new store could not open the same database)."""
    store = InMemoryKeyStore()
    return lambda *args: main(list(args), store)


def test_save_load_list_and_forget(data: Path) -> None:
    assert load_mapping("a.csv") is None
    save_mapping("a.csv", SavedMapping({"company": "Employer"}, "Sheet1", "Tufts"))
    assert load_mapping("a.csv") == SavedMapping({"company": "Employer"}, "Sheet1", "Tufts")
    assert list_mappings() == {"a.csv": ["company"]}
    assert oct(mappings_path().stat().st_mode & 0o777) == "0o600"
    assert forget_mapping("a.csv") and not forget_mapping("a.csv")
    assert list_mappings() == {}


def test_a_corrupt_file_is_treated_as_empty(data: Path) -> None:
    mappings_path().write_text("not json", encoding="utf-8")
    assert load_mapping("a.csv") is None and list_mappings() == {}


def test_a_mapping_given_once_is_reused_for_the_same_file_name(
    run: Run, data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = data / "alumni.csv"
    first.write_text(ODD, encoding="utf-8")
    assert run("alumni", "import", str(first), "--map", "company=Current Employer") == 0
    assert "1 added" in capsys.readouterr().out
    first.write_text(NEWER, encoding="utf-8")  # next term's export, same name, no flags
    assert run("alumni", "import", str(first)) == 0
    assert "1 added" in capsys.readouterr().out
    assert run("alumni", "mappings") == 0
    assert "alumni.csv: company" in capsys.readouterr().out
    assert "Current Employer" not in capsys.readouterr().out  # headers are not listed


def test_flags_win_over_the_saved_mapping_and_update_it(run: Run, data: Path) -> None:
    path = data / "alumni.csv"
    path.write_text(ODD, encoding="utf-8")
    run("alumni", "import", str(path), "--map", "company=Group")
    run("alumni", "import", str(path), "--map", "company=Current Employer", "--group", "Tufts")
    assert load_mapping("alumni.csv") == SavedMapping(
        {"company": "Current Employer"}, None, "Tufts"
    )
    saved = json.loads(mappings_path().read_text(encoding="utf-8"))
    assert "Ada" not in json.dumps(saved)  # no people in the file


def test_preview_never_saves(run: Run, data: Path) -> None:
    path = data / "alumni.csv"
    path.write_text(ODD, encoding="utf-8")
    run("alumni", "import", str(path), "--map", "company=Current Employer", "--preview")
    assert load_mapping("alumni.csv") is None


def test_a_changed_header_is_a_clear_error_not_a_wrong_mapping(
    run: Run, data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = data / "alumni.csv"
    path.write_text(ODD, encoding="utf-8")
    run("alumni", "import", str(path), "--map", "company=Current Employer")
    capsys.readouterr()
    path.write_text(CHANGED, encoding="utf-8")
    assert run("alumni", "import", str(path)) == 1
    out = capsys.readouterr().out
    assert "no column called 'Current Employer'" in out and "hub alumni forget alumni.csv" in out


def test_forget_removes_it(run: Run, data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    save_mapping("alumni.csv", SavedMapping({"company": "X"}))
    assert run("alumni", "forget", "somewhere/alumni.csv") == 0
    assert run("alumni", "forget", "alumni.csv") == 1
    assert run("alumni", "mappings") == 0
    assert "No saved column mappings." in capsys.readouterr().out
