"""The conversation panel, part 1: read-only questions (CH-158). Fake models, saved data."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (saved-data helper)

from careerhub.core.migrate import migrate
from careerhub.core.models import ConversationMessage, Job
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import BudgetExceeded, Destination, PrivacyTier, Router
from careerhub.llm.fake import FakeProvider
from careerhub.llm.providers import ProviderError
from careerhub.llm.setup import NoRoute
from careerhub.modules.conversation import MAX_ANSWER_CHARS, ask, build_context
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(
    session: Session, provider: FakeProvider | None = None, no_route: bool = False
) -> TestClient:
    def opener() -> tuple[Router, list[str]]:
        if no_route or provider is None:
            raise NoRoute("none")
        return Router([provider]), [provider.name]

    app = create_app(
        TOKEN, PORT, lambda: True, session_factory(session.get_bind()), open_router=opener
    )
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def post(c: TestClient, body: str) -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/conversation").text)
    assert token
    return c.post(
        "/conversation", headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}&{body}"
    )


def test_a_question_is_answered_from_the_stored_explanation_and_saved(session: Session) -> None:
    add_job(session, 1, 90)
    fake = FakeProvider("m", replies=["It ranks first because the field matches well."])
    c = web(session, fake)
    response = post(c, "question=why+is+%231+ranked+first%3F")
    assert response.status_code == 303 and response.headers["location"] == "/conversation"
    prompt = fake.requests[0].prompt
    assert "Role 1 at Acme" in prompt and "Strong match. It fits." in prompt
    assert "why is #1 ranked first?" in prompt
    page = c.get("/conversation").text
    assert "You:" in page and "ranks first because the field matches" in page
    assert session.query(ConversationMessage).count() == 2


def test_the_request_is_personal_tier_not_essential_and_redacted(session: Session) -> None:
    job = add_job(session, 1, 80)
    session.query(Job).one().structured_fields = {"fields": {"summary": "Email jane@example.com"}}
    session.commit()
    fake = FakeProvider("m", replies=["ok"])
    router = Router([fake])
    context = build_context(session, None, "Jane Sample", [], job.id)
    ask(
        session,
        router,
        ["m"],
        "Is Jane Sample a fit? write jane@example.com",
        context,
        "Jane Sample",
        [],
        job.id,
    )
    request = fake.requests[0]
    assert request.tier is PrivacyTier.PERSONAL and request.essential is False
    assert "jane@example.com" not in request.prompt and "Jane Sample" not in request.prompt


def test_a_cloud_model_that_trains_on_data_never_sees_the_question(session: Session) -> None:
    add_job(session, 1, 80)
    training = FakeProvider("free", Destination.CLOUD, trains_on_data=True, replies=["x"])
    result = ask(session, Router([training]), ["free"], "why?", "ctx", "", [])
    assert not result.ok and "not allowed to see personal data" in result.text
    assert training.requests == [] and session.query(ConversationMessage).count() == 0


def test_no_answer_is_never_saved_and_the_page_says_why(session: Session) -> None:
    add_job(session, 1, 80)
    c = web(session, no_route=True)
    page = post(c, "question=hello")
    assert page.status_code == 200 and "No model is set up for questions yet" in page.text
    assert 'role="alert"' in page.text and session.query(ConversationMessage).count() == 0


def test_the_budget_pause_and_a_failing_model_are_messages_not_errors(session: Session) -> None:
    class OverBudget(Router):
        def complete(self, request: Any, provider: str, accept: Any = None) -> Any:
            raise BudgetExceeded("over")

    paused = ask(session, OverBudget([FakeProvider("m")]), ["m"], "hi", "ctx", "", [])
    assert not paused.ok and "budget" in paused.text

    class Failing(FakeProvider):
        def complete(self, request: Any) -> Any:
            raise ProviderError("down")

    good = FakeProvider("backup", replies=["from the backup"])
    fallback = ask(
        session, Router([Failing("main"), good]), ["main", "backup"], "hi", "ctx", "", []
    )
    assert fallback.ok and fallback.text == "from the backup"  # the next model on the route answers
    nobody = ask(session, Router([Failing("main")]), ["main"], "hi", "ctx", "", [])
    assert not nobody.ok and "No model could answer" in nobody.text


def test_empty_or_oversized_questions_and_answers_are_handled(session: Session) -> None:
    fake = FakeProvider("m", replies=["x" * (MAX_ANSWER_CHARS + 500)])
    router = Router([fake])
    assert ask(session, router, ["m"], "   ", "ctx", "", []).text == "Type a question first."
    result = ask(session, router, ["m"], "q " * 1000, "ctx", "", [])
    assert result.ok and len(result.text) == MAX_ANSWER_CHARS
    assert len(fake.requests[0].prompt) < 6000  # the question was cut


def test_hostile_text_is_escaped_and_the_form_needs_csrf(session: Session) -> None:
    add_job(session, 1, 80)
    fake = FakeProvider("m", replies=["<script>alert(1)</script> and <img src=x onerror=alert(2)>"])
    c = web(session, fake)
    post(c, "question=%3Cscript%3Ealert(3)%3C%2Fscript%3E")
    html = c.get("/conversation").text
    assert "<script>alert" not in html and "<img src=x" not in html and "&lt;script&gt;" in html
    refused = c.post("/conversation", headers={**ORIGIN, **FORM}, content="question=hi")
    assert refused.status_code == 403


def test_a_job_page_question_is_kept_with_that_job_and_the_page_is_accessible(
    session: Session,
) -> None:
    job = add_job(session, 1, 80)
    add_job(session, 2, 70)
    fake = FakeProvider("m", replies=["About job one."])
    c = web(session, fake)
    token = re.search(r'name="csrf" value="([^"]+)"', c.get(f"/conversation?job={job.id}").text)
    assert token
    c.post(
        "/conversation",
        headers={**ORIGIN, **FORM},
        content=f"csrf={token.group(1)}&job={job.id}&question=tell+me",
    )
    assert "Role 1 at Acme" in fake.requests[0].prompt and "Role 2" not in fake.requests[0].prompt
    page = c.get(f"/conversation?job={job.id}")
    assert "About job one." in page.text and check_page(page.text) == []
    assert "About job one." not in c.get("/conversation?job=2").text
    assert 'href="/conversation?job=1"' in c.get("/jobs/1").text
    assert 'href="/conversation"' in c.get("/today").text


def test_a_huge_or_bad_job_number_is_ignored(session: Session) -> None:
    fake = FakeProvider("m", replies=["ok"])
    c = web(session, fake)
    assert post(c, f"job={'9' * 30}&question=hi").status_code == 303
    assert post(c, "job=abc&question=hi").status_code == 303
    assert c.get("/conversation?job=999").status_code == 200
