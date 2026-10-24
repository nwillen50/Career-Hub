"""Every command in `hub`'s USAGE is dispatched (CH-131).

Merge conflicts once dropped commands silently. This runs each USAGE line once against an empty
data folder with no network. A command may fail for a good reason (nothing to show, missing
file), but it must never fall through to the usage text, which is what a dropped command does.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from careerhub.cli import USAGE, main
from careerhub.core.secrets import InMemoryKeyStore

# One concrete invocation per USAGE form. Keep in step with USAGE: the coverage test below fails
# when a USAGE line has no entry here.
INVOCATIONS: dict[str, list[str]] = {
    "profile show": ["profile", "show"],
    "ingest": ["ingest", "greenhouse", "--company", "acme"],
    "plan": ["plan"],
    "paths": ["paths", "Acme"],
    "people find": ["people", "find", "ada"],
    "people tag": ["people", "tag", "1", "met", "at", "a", "talk"],
    "people import-alumni": ["people", "import-alumni", "MISSING.csv", "--preview"],
    "alumni import": ["alumni", "import", "MISSING.csv", "--preview"],
    "alumni mappings": ["alumni", "mappings"],
    "schedule install": ["schedule", "install"],
    "nightly": ["nightly"],
    "refresh": ["refresh"],
    "backup list": ["backup", "list"],
    "attention": ["attention", "--json"],
    "web": ["web"],
    "menubar": ["menubar"],
    "demo": ["demo"],
    "add-job": [
        "add-job",
        "https://example.com/jobs/1",
        "--company",
        "Acme",
        "--title",
        "Engineer",
    ],
    "jobs close": ["jobs", "close", "1"],
    "companies": ["companies", "suggestions"],
    "companies merges": ["companies", "merges"],
    "companies split": ["companies", "split", "Acme", "Acme Robotics"],
    "targets find-boards": ["targets", "find-boards"],
    "targets list": ["targets", "list"],
    "targets enable-matching": ["targets", "enable-matching"],
    "targets enable": ["targets", "enable", "Acme"],
    "targets add": ["targets", "add", "Acme"],
    "models list": ["models", "list"],
    "models routes": ["models", "routes"],
    "models remove": ["models", "remove", "gemini"],
    "cache clear": ["cache", "clear"],
    "profile ready": ["profile", "ready"],
    "profile facts": ["profile", "facts"],
    "score --all": ["score", "--all"],
    "shortlist build": ["shortlist", "build"],
    "sources list": ["sources", "list"],
    "sources add": ["sources", "add", "greenhouse", "Acme", "https://boards.example.com/acme"],
    "sources approve": ["sources", "approve", "1"],
}

# USAGE words that start a command form, mapped to the INVOCATIONS keys that cover them.
COVERS = {
    "hub profile show": "profile show",
    "hub ingest": "ingest",
    "hub plan": "plan",
    "hub paths": "paths",
    "hub people find": "people find",
    "hub people tag": "people tag",
    "hub people import-alumni": "people import-alumni",
    "hub alumni mappings": "alumni mappings",
    "hub schedule install": "schedule install",
    "hub nightly": "nightly",
    "hub refresh": "refresh",
    "hub backup list": "backup list",
    "hub attention": "attention",
    "hub web": "web",
    "hub menubar": "menubar",
    "hub demo": "demo",
    "hub add-job": "add-job",
    "hub jobs close": "jobs close",
    "hub companies": "companies",
    "hub companies merges": "companies merges",
    "hub companies split": "companies split",
    "hub targets find-boards": "targets find-boards",
    "hub targets enable": "targets enable",
    "hub targets add": "targets add",
    "hub models list": "models list",
    "hub models add": "models list",  # asks for a key at a hidden prompt; list proves the group
    "hub models remove": "models remove",
    "hub models add-custom": "models list",
    "hub cache clear": "cache clear",
    "hub profile ready": "profile ready",
    "hub profile facts": "profile facts",
    "hub score --all": "score --all",
    "hub shortlist build": "shortlist build",
    "hub sources list": "sources list",
    "hub sources add": "sources add",
    "hub sources approve": "sources approve",
}


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty data folder, empty home (so no launch agent lands in the real one), no network."""
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def no_network(*args: object, **kwargs: object) -> None:
        raise OSError("network is off in tests")

    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)  # `hub web` must not serve
    monkeypatch.setattr("builtins.input", lambda *args: "n")  # any yes/no question gets "no"
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)


def usage_forms() -> list[str]:
    """Return the start of each command form in USAGE, such as 'hub people tag'."""
    forms = []
    for line in USAGE.splitlines():
        match = re.match(r"  (hub (?:[a-z-]+)(?: (?:[a-z-]+))?)", line)
        if match:
            forms.append(match.group(1))
    return forms


def covering_key(form: str) -> str | None:
    """Find the INVOCATIONS key for a USAGE form, trying the form and then shorter prefixes."""
    words = form.split()
    for size in range(len(words), 1, -1):
        key = " ".join(words[:size])
        if key in COVERS:
            return COVERS[key]
    return None


def test_every_usage_line_has_a_test_invocation() -> None:
    uncovered = [f for f in usage_forms() if covering_key(f) is None]
    assert not uncovered, f"add these USAGE forms to COVERS/INVOCATIONS: {uncovered}"
    assert all(COVERS[k] in INVOCATIONS for k in COVERS)


@pytest.mark.parametrize("name", sorted(INVOCATIONS))
def test_command_is_dispatched(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryKeyStore()
    try:
        code = main(INVOCATIONS[name], store)
    except OSError as error:
        assert "network is off" in str(error)  # it tried to reach the network: it was dispatched
        return
    out = capsys.readouterr().out
    assert "Usage:" not in out, f"hub {' '.join(INVOCATIONS[name])} fell through to the usage text"
    assert code != 2
