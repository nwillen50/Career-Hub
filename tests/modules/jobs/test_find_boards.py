"""Tests for finding company job boards (CH-112). Web answers are all stubs; nothing goes online."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.find_boards import candidate_tokens, find_boards
from careerhub.modules.jobs.targets import Target, load_targets, save_targets, set_unsupported


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def client(answers: dict[str, httpx.Response]) -> httpx.Client:
    """A web client that answers from `answers` (matched by URL fragment); others get 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, reply in answers.items():
            if fragment in str(request.url):
                return reply
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def greenhouse(company: str) -> httpx.Response:
    return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Eng", "company_name": company}]})


def test_candidate_tokens_drop_generic_words_and_stay_valid() -> None:
    tokens = candidate_tokens("Northwind Labs, Inc.")
    assert tokens[0] == "northwindlabsinc" and "northwind" in tokens and "northwindhq" in tokens
    assert len(tokens) == len(set(tokens)) <= 8
    assert candidate_tokens("!!!") == []


def test_a_verified_hit_is_saved_and_proposed(session: Session) -> None:
    targets = [Target(name="Northwind Labs", enabled=True)]
    web = client({"boards/northwind/jobs": greenhouse("Northwind")})
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert [name for name, _ in report.found] == ["Northwind Labs"]
    assert (targets[0].board_kind, targets[0].board) == ("greenhouse", "northwind")
    source = session.scalars(select(Source)).one()
    assert source.status == "proposed" and source.config == {"board": "northwind"}


def test_a_miss_changes_nothing(session: Session) -> None:
    targets = [Target(name="Nowhere Co", enabled=True)]
    report = find_boards(session, targets, client({}), sleep=lambda _: None)
    assert report.missed == ["Nowhere Co"] and targets[0].board is None
    assert session.scalars(select(Source)).all() == []


def test_a_board_for_another_company_is_only_uncertain(session: Session) -> None:
    targets = [Target(name="Acme", enabled=True)]
    web = client({"boards/acme/jobs": greenhouse("Totally Different Inc")})
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert [n for n, _ in report.uncertain] == ["Acme"] and not report.found
    assert targets[0].board is None and session.scalars(select(Source)).all() == []


def test_disabled_known_and_unsupported_targets_are_skipped(session: Session) -> None:
    targets = [
        Target(name="Off Co"),
        Target(name="Known Co", enabled=True, board_kind="lever", board="known"),
        Target(name="Work Day Co", enabled=True, unsupported_ats="Workday"),
    ]
    seen: list[str] = []
    web = httpx.Client(
        transport=httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(404))
    )
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert seen == [] and report == type(report)()


def test_requests_are_spaced_out(session: Session) -> None:
    pauses: list[float] = []
    find_boards(session, [Target(name="Zed", enabled=True)], client({}), pauses.append)
    assert pauses and all(p >= 1.0 for p in pauses)
    assert len(pauses) == len(candidate_tokens("Zed")) * 3 - 1


def test_a_slow_down_answer_stops_the_search(session: Session) -> None:
    targets = [Target(name="Zed", enabled=True), Target(name="Yak", enabled=True)]
    web = client({"": httpx.Response(429)})
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert report.stopped and "slow down" in report.stopped and not report.missed


def test_a_network_error_leaves_targets_unchanged(session: Session) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    targets = [Target(name="Zed", enabled=True)]
    web = httpx.Client(transport=httpx.MockTransport(boom))
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert report.missed == ["Zed"] and targets[0].board is None


def test_a_page_that_is_not_json_is_a_miss(session: Session) -> None:
    web = client({"": httpx.Response(200, text="<html>hi</html>")})
    report = find_boards(session, [Target(name="Zed", enabled=True)], web, sleep=lambda _: None)
    assert report.missed == ["Zed"]


def test_lever_hits_are_matched_by_company_name_in_the_postings(session: Session) -> None:
    postings = [{"text": "Engineer", "categories": {"team": "Zed Robotics"}}]
    web = client({"postings/zed": httpx.Response(200, json=postings)})
    targets = [Target(name="Zed Robotics", enabled=True)]
    report = find_boards(session, targets, web, sleep=lambda _: None)
    assert targets[0].board_kind == "lever" and len(report.found) == 1


def test_note_marks_an_unsupported_system() -> None:
    targets = [Target(name="Work Day Co")]
    assert set_unsupported(targets, "work day co", "  Workday ") is False
    assert targets[0].unsupported_ats == "Workday"
    assert set_unsupported(targets, "nobody", "x") is True


def test_hub_targets_note_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    save_targets([Target(name="Work Day Co", enabled=True)])
    store = InMemoryKeyStore()
    assert main(["targets", "note", "Work Day Co", "Workday"], store) == 0
    assert load_targets()[0].unsupported_ats == "Workday"
    assert main(["targets", "list"], store) == 0
    assert "not supported (Workday)" in capsys.readouterr().out
    assert main(["targets", "note", "Nobody", "Workday"], store) == 1
