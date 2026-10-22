"""Tests for target companies (CH-97). The seed list is public data from The Engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.modules.jobs.targets import (
    Target,
    TargetsError,
    load_targets,
    matches_interests,
    merge_import,
    read_targets,
    save_targets,
    set_enabled,
)

SEED = Path(__file__).parents[3] / "docs" / "seeds" / "engine-resident-companies.yaml"


def test_the_committed_seed_list_loads_and_everything_starts_disabled() -> None:
    targets = read_targets(SEED)
    assert len(targets) == 137 and all(t.sectors for t in targets)
    merged, added = merge_import([], targets)
    assert added == 137 and not any(t.enabled for t in merged)


def test_importing_twice_changes_nothing_and_keeps_the_users_choices() -> None:
    mine = [Target(name="Acme Robotics", enabled=True, board_kind="lever", board="acme")]
    incoming = [Target(name="ACME ROBOTICS", sectors=["ROBOTICS"]), Target(name="Globex")]
    merged, added = merge_import(mine, incoming)
    assert (
        added == 1
        and merged[0] == mine[0]
        and [t.name for t in merged] == ["Acme Robotics", "Globex"]
    )
    again, added_again = merge_import(merged, incoming + incoming)
    assert added_again == 0 and again == merged


@pytest.mark.parametrize(
    ("interests", "sectors", "expected"),
    [
        (["robotics"], ["AUTONOMOUS SYSTEMS / ROBOTICS"], True),
        (["Biotech"], ["BIOTECH / LIFE SCIENCES"], True),
        (["life sciences"], ["BIOTECH / LIFE SCIENCES"], True),
        (["climate"], ["ENERGY", "MOBILITY"], False),
        (["bio"], ["BIOTECH / LIFE SCIENCES"], False),  # whole words only
        ([], ["ROBOTICS"], False),
        ([""], ["ROBOTICS"], False),
        (["robotics"], [], False),
    ],
)
def test_interests_match_whole_words_of_a_sector(
    interests: list[str], sectors: list[str], expected: bool
) -> None:
    assert matches_interests(Target(name="X", sectors=sectors), interests) is expected


def test_enable_and_disable_report_unknown_names() -> None:
    targets = [Target(name="Acme"), Target(name="Globex")]
    assert set_enabled(targets, ["GLOBEX", "Nope"], True) == ["Nope"]
    assert [t.enabled for t in targets] == [False, True]


def test_a_saved_file_is_private_and_loads_back(tmp_path: Path) -> None:
    path = tmp_path / "targets.yaml"
    save_targets([Target(name="Acme", sectors=["ROBOTICS"], enabled=True)], path)
    assert load_targets(path) == [Target(name="Acme", sectors=["ROBOTICS"], enabled=True)]
    assert path.stat().st_mode & 0o077 == 0
    assert load_targets(tmp_path / "missing.yaml") == []


@pytest.mark.parametrize(
    "text",
    [
        "targets: [",
        "targets: [{name: ''}]",
        "targets: [{name: A, board_kind: workday}]",
        "extra: 1",
    ],
)
def test_bad_files_are_errors_that_do_not_echo_their_contents(tmp_path: Path, text: str) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TargetsError) as raised:
        read_targets(path)
    assert text not in str(raised.value)


def test_the_commands_import_list_enable_and_match_interests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    assert main(["targets", "import", str(SEED)], store) == 0
    assert "Added 137 companies (disabled)" in capsys.readouterr().out
    assert main(["targets", "import", str(SEED)], store) == 0
    assert "Added 0 companies" in capsys.readouterr().out
    assert main(["targets", "enable", "100XBIO", "Nope"], store) == 1
    assert "No target named 'Nope'" in capsys.readouterr().out
    assert main(["targets", "enable-matching"], store) == 1  # no profile yet
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text(
        "---\nname: Jane\ninterests: [robotics]\n---\n"
    )
    assert main(["targets", "enable-matching"], store) == 0
    capsys.readouterr()
    main(["targets", "list"], store)
    out = capsys.readouterr().out
    assert "[x] 100XBIO" in out and "137 companies" in out
    assert main(["targets", "disable", "100XBIO"], store) == 0
    assert main(["targets", "bogus"], store) == 2
    assert main(["targets", "import", str(tmp_path / "nope.yaml")], store) == 1
