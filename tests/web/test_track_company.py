"""The 'Follow this company' button on Job detail (CH-198). A mocked HTTP client; no network."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job

from careerhub.core.migrate import migrate
from careerhub.core.models import Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.targets import load_targets
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session, requests: list[str], boards: set[str]) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "boards-api.greenhouse.io" and any(
            request.url.path.endswith(f"/{b}/jobs") for b in boards
        ):
            return httpx.Response(200, json={"jobs": [{"company_name": "Acme", "title": "x"}]})
        return httpx.Response(404)

    app = create_app(
        TOKEN, PORT, lambda: True, session_factory(session.get_bind()),
        track_client=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    client.get(f"/?t={TOKEN}")
    return client


def post(client: TestClient, path: str) -> httpx.Response:
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/today").text)
    assert token
    return client.post(path, headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}")


def test_the_button_is_on_job_detail_and_the_page_load_makes_no_request(session: Session) -> None:
    job = add_job(session, 1, 80, company="Acme")
    requests: list[str] = []
    html = web(session, requests, set()).get(f"/jobs/{job.id}").text
    assert f'action="/jobs/{job.id}/track"' in html and "Follow Acme" in html
    assert requests == [] and check_page(html) == []


def test_pressing_it_follows_the_company_and_proposes_the_board_it_finds(session: Session) -> None:
    job = add_job(session, 1, 80, company="Acme")
    requests: list[str] = []
    reply = post(web(session, requests, {"acme"}), f"/jobs/{job.id}/track")
    assert reply.status_code == 200 and "proposed" in reply.text and check_page(reply.text) == []
    assert [(t.name, t.enabled, t.board) for t in load_targets()] == [("Acme", True, "acme")]
    proposed = session.query(Source).filter(Source.url.like("%greenhouse%")).all()
    assert proposed and proposed[0].status == "proposed"
    assert all("greenhouse.io" in u or "lever.co" in u or "ashbyhq.com" in u for u in requests)


def test_no_board_found_says_so_and_still_follows(session: Session) -> None:
    job = add_job(session, 1, 80, company="Acme")
    reply = post(web(session, [], set()), f"/jobs/{job.id}/track")
    assert "No board found" in reply.text and load_targets()[0].enabled


def test_it_needs_the_csrf_token_and_a_real_job(session: Session) -> None:
    job = add_job(session, 1, 80)
    requests: list[str] = []
    client = web(session, requests, set())
    refused = client.post(f"/jobs/{job.id}/track", headers={**ORIGIN, **FORM}, content="")
    assert refused.status_code == 403 and requests == []
    assert post(client, "/jobs/999/track").status_code == 404


def test_the_button_is_on_today_and_browse_cards_and_loading_makes_no_request(
    session: Session,
) -> None:
    job = add_job(session, 1, 80, company="Acme")
    requests: list[str] = []
    client = web(session, requests, set())
    for page in ("/today", "/browse"):
        html = client.get(page).text
        assert f'action="/jobs/{job.id}/track"' in html and ">Follow Acme<" in html, page
        assert check_page(html) == [], page
    assert requests == []


def test_pressing_the_card_button_follows_the_company(session: Session) -> None:
    job = add_job(session, 1, 80, company="Acme")
    reply = post(web(session, [], {"acme"}), f"/jobs/{job.id}/track")
    assert reply.status_code == 200 and [t.name for t in load_targets()] == ["Acme"]
