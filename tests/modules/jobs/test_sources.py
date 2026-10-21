from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    SourceNotApproved,
    UnknownSource,
    approve_source,
    approved_sources,
    disable_source,
    list_sources,
    propose_source,
    reject_source,
    require_approved,
)

URL = "https://boards.greenhouse.io/acme"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_a_proposed_source_is_not_used_until_the_user_approves(session: Session) -> None:
    """The PRD's acceptance test: nothing is added without the user's approval."""
    source = propose_source(session, "greenhouse", "Acme", URL)
    assert source.status == "proposed" and source.decided_at is None
    assert approved_sources(session) == []
    with pytest.raises(SourceNotApproved):
        require_approved(source)
    approve_source(session, source.id)
    assert approved_sources(session) == [source]
    assert require_approved(source) is source and source.decided_at is not None


def test_rejected_and_disabled_sources_cannot_be_used(session: Session) -> None:
    rejected = propose_source(session, "greenhouse", "A", URL + "1")
    disabled = propose_source(session, "greenhouse", "B", URL + "2")
    reject_source(session, rejected.id)
    approve_source(session, disabled.id)
    disable_source(session, disabled.id)
    assert approved_sources(session) == []
    for source in (rejected, disabled):
        with pytest.raises(SourceNotApproved):
            require_approved(source)


def test_proposing_the_same_url_again_changes_nothing(session: Session) -> None:
    first = propose_source(session, "greenhouse", "Acme", URL)
    approve_source(session, first.id)
    again = propose_source(session, "greenhouse", "Acme again", URL)
    assert again.id == first.id and again.status == "approved"
    assert len(list_sources(session)) == 1


def test_approved_sources_can_be_filtered_by_kind(session: Session) -> None:
    a = propose_source(session, "greenhouse", "A", URL + "1")
    b = propose_source(session, "lever", "B", URL + "2")
    approve_source(session, a.id)
    approve_source(session, b.id)
    assert approved_sources(session, "lever") == [b]


def test_unknown_id_is_an_error(session: Session) -> None:
    with pytest.raises(UnknownSource):
        approve_source(session, 999)


def test_hub_sources_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    assert main(["sources", "add", "greenhouse", "Acme", URL], store) == 0
    assert "is proposed" in capsys.readouterr().out
    assert main(["sources", "list"], store) == 0
    assert "proposed" in capsys.readouterr().out
    assert main(["sources", "approve", "1"], store) == 0
    assert "now approved" in capsys.readouterr().out
    assert main(["sources", "approve", "7"], store) == 1
    assert main(["sources", "bogus"], store) == 2
