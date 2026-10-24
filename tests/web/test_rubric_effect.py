"""'Check the effect' of an accepted rubric change: ranking eval before and after (CH-172)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import LLMRequest, LLMResponse, Router
from careerhub.llm.cost import BudgetExceeded
from careerhub.llm.provider import Destination
from careerhub.modules.proposals import RubricChange, accept
from careerhub.modules.rubric_effect import (
    accepted_rubric_changes,
    estimate_calls,
    labels_path,
    rubric_before,
)
from careerhub.modules.rubric_proposals import propose_rubric_change
from careerhub.profile.rubric import rubric_path
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
SAMPLE = Path(__file__).parents[2] / "evals" / "ranking" / "sample.yaml"
RUBRIC = "version: 1\ncriteria:\n  - {name: field, weight: 1, description: Right field.}\n"
ADD_TITLE_FIT = RubricChange(action="add", name="title fit", weight=3, description="Title fits.")
PROFILE = "---\nname: Jane Sample\nskills: [Python]\n---\n"
GOOD = {
    "Machine Learning Engineer, New Grad": 9,
    "Data Analyst": 6,
    "Senior Sales Director": 1,
}


class Scripted:
    """Scores flat (5) unless the rubric has 'title fit', then by the job title."""

    name = "m"
    model = "m-1"
    destination = Destination.LOCAL
    trains_on_data = False

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.fail:
            raise self.fail
        if "extract structured facts" in request.prompt:
            return LLMResponse(text=json.dumps({"level": "unknown"}), provider="m", model="m-1")
        smart = "title fit" in request.prompt.lower()
        score = (
            next((v for t, v in GOOD.items() if f"Title: {t}" in request.prompt), 5) if smart else 5
        )
        names = ["field", "title fit"] if smart else ["field"]
        answer = {
            "criteria": [{"name": n, "score": score, "reason": "r"} for n in names],
            "explanation": "ok",
        }
        return LLMResponse(text=json.dumps(answer), provider="m", model="m-1")


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    profile = tmp_path / "data" / "profile"
    profile.mkdir(parents=True)
    rubric_path().write_text(RUBRIC, encoding="utf-8")
    (profile / "profile.md").write_text(PROFILE, encoding="utf-8")
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def accepted(session: Session) -> int:
    proposal = propose_rubric_change(session, ADD_TITLE_FIT)
    accept(session, proposal.id)
    return proposal.id


def web(session: Session, provider: Scripted | None = None) -> TestClient:
    router = Router([provider or Scripted()])
    app = create_app(
        TOKEN, PORT, lambda: True, session_factory(session.get_bind()),
        open_eval=lambda: (router, ["m"], ["m"]),
    )  # fmt: skip
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    client.get(f"/?t={TOKEN}")
    return client


def post(client: TestClient, path: str) -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/conversation").text)
    assert token
    return client.post(path, headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}")


def add_labels() -> None:
    labels_path().parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE, labels_path())


def test_the_old_rubric_is_found_from_the_backup_kept_when_it_was_accepted(
    session: Session,
) -> None:
    proposal_id = accepted(session)
    (proposal,) = accepted_rubric_changes(session)
    assert proposal.id == proposal_id
    old = rubric_before(proposal)
    assert old is not None and [c.name for c in old.criteria] == ["field"] and old.version == 1
    for backup in rubric_path().parent.glob("rubric.yaml.before-*"):
        backup.unlink()
    assert rubric_before(proposal) is None


def test_the_estimate_is_two_rubrics_times_reading_and_scoring() -> None:
    assert estimate_calls(30) == 120 and estimate_calls(0) == 0


def test_the_conversation_page_offers_the_check_only_for_accepted_rubric_changes(
    session: Session,
) -> None:
    client = web(session)
    assert "Check the effect" not in client.get("/conversation").text
    proposal_id = accepted(session)
    page = client.get("/conversation").text
    assert f'href="/proposals/{proposal_id}/effect"' in page and "Check the effect" in page


def test_without_labeled_postings_it_says_so_and_offers_no_button(session: Session) -> None:
    proposal_id = accepted(session)
    html = web(session).get(f"/proposals/{proposal_id}/effect").text
    assert "no labeled postings yet" in html and "Run the check (up to" not in html
    assert check_page(html) == []


def test_the_page_says_roughly_how_many_calls_before_anything_runs(session: Session) -> None:
    proposal_id = accepted(session)
    add_labels()
    html = web(session).get(f"/proposals/{proposal_id}/effect").text
    assert "up to 12 model calls" in html and "Run the check (up to 12 calls)" in html
    assert "Only 3 labeled postings" in html and "about 30" in html
    assert check_page(html) == []


def test_pressing_the_button_shows_before_and_after(session: Session) -> None:
    proposal_id = accepted(session)
    add_labels()
    client = web(session)
    result = post(client, f"/proposals/{proposal_id}/effect")
    assert result.status_code == 200
    html = result.text
    assert "Before and after (3 labeled postings)" in html
    row = re.search(r"Label pairs in the right order</td><td>([^<]+)</td><td>([^<]+)</td>", html)
    assert row and row.group(2) == "100%" and row.group(1) != "100%"
    assert "Nothing was changed" in html and check_page(html) == []


def test_it_never_runs_from_a_plain_page_load_or_without_the_csrf_token(session: Session) -> None:
    proposal_id = accepted(session)
    add_labels()
    spy = Scripted()
    calls: list[LLMRequest] = []
    original = spy.complete
    spy.complete = lambda request: (calls.append(request), original(request))[1]  # type: ignore[method-assign]
    client = web(session, spy)
    client.get(f"/proposals/{proposal_id}/effect")
    assert calls == []
    refused = client.post(
        f"/proposals/{proposal_id}/effect", headers={**ORIGIN, **FORM}, content=""
    )
    assert refused.status_code == 403 and calls == []


def test_problems_with_the_models_are_said_in_plain_words(session: Session) -> None:
    proposal_id = accepted(session)
    add_labels()
    over = post(web(session, Scripted(BudgetExceeded("x"))), f"/proposals/{proposal_id}/effect")
    assert "budget is used up" in over.text and "Traceback" not in over.text


def test_unknown_or_pending_proposals_are_not_found(session: Session) -> None:
    client = web(session)
    assert client.get("/proposals/999/effect").status_code == 404
    pending = propose_rubric_change(session, ADD_TITLE_FIT)
    assert client.get(f"/proposals/{pending.id}/effect").status_code == 404
