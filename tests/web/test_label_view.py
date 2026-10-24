"""The 'Label for evals' view (CH-201): great, okay, no, saved in the eval file format."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job

from careerhub.core.migrate import migrate
from careerhub.core.models import Feedback
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.labeling import counts, labels_path, next_to_label
from careerhub.modules.jobs.rank_evals import load_labeled
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


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    client.get(f"/?t={TOKEN}")
    return client


def post(client: TestClient, body: str) -> object:
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/browse/label").text)
    assert token
    return client.post(
        "/browse/label", headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}&{body}"
    )


def test_the_view_shows_one_posting_at_a_time_best_first_with_three_buttons(
    session: Session,
) -> None:
    add_job(session, 1, 70, title="Second")
    add_job(session, 2, 90, title="First")
    html = web(session).get("/browse/label").text
    assert "First" in html and "Second" not in html
    for label in ("great", "okay", "no"):
        assert f'name="label" value="{label}"' in html
    assert "/browse/label?skip=1" in html and check_page(html) == []
    assert "Second" in web(session).get("/browse/label?skip=1").text


def test_saving_a_label_writes_the_eval_file_the_ranking_eval_reads(session: Session) -> None:
    job = add_job(session, 1, 90, title="Robotics Engineer", company="Acme")
    client = web(session)
    reply = post(client, f"job={job.id}&label=great&reason=Right+city+and+field")
    assert reply.status_code == 303  # type: ignore[attr-defined]
    (case,) = load_labeled(labels_path())
    assert (case.id, case.company, case.title, case.label) == (
        f"job-{job.id}", "Acme", "Robotics Engineer", "great",
    )  # fmt: skip
    assert case.reason == "Right city and field" and counts() == {"great": 1, "okay": 0, "no": 0}
    assert "No more postings" in client.get("/browse/label").text  # the only job is labeled


def test_a_label_replaces_an_older_one_and_a_reaction_is_not_a_label(session: Session) -> None:
    job = add_job(session, 1, 90)
    other = add_job(session, 2, 80, title="Reacted")
    session.add(Feedback(job_id=other.id, reaction="interested"))
    session.commit()
    client = web(session)
    post(client, f"job={job.id}&label=okay&reason=")
    post(client, f"job={job.id}&label=no&reason=changed+my+mind")
    cases = yaml.safe_load(labels_path().read_text(encoding="utf-8"))["cases"]
    assert [(c["id"], c["label"]) for c in cases] == [(f"job-{job.id}", "no")]
    assert cases[0]["reason"] == "changed my mind"
    assert next_to_label(session) is None  # the reacted job is not offered
    assert session.query(Feedback).count() == 1  # labeling made no reaction


def test_bad_input_is_refused_and_a_broken_file_is_never_overwritten(session: Session) -> None:
    job = add_job(session, 1, 90)
    client = web(session)
    assert "Choose great" in post(client, f"job={job.id}&label=maybe").text  # type: ignore[attr-defined]
    assert "under 300" in post(client, f"job={job.id}&label=great&reason={'x' * 400}").text  # type: ignore[attr-defined]
    assert "gone" in post(client, "job=999&label=great").text  # type: ignore[attr-defined]
    assert not labels_path().exists()
    labels_path().parent.mkdir(parents=True, exist_ok=True)
    labels_path().write_text("cases: [unclosed", encoding="utf-8")
    reply = post(client, f"job={job.id}&label=great")
    assert "could not be read" in reply.text  # type: ignore[attr-defined]
    assert labels_path().read_text(encoding="utf-8") == "cases: [unclosed"


def test_hostile_posting_text_is_escaped_on_the_page(session: Session) -> None:
    job = add_job(session, 1, 90, title="<script>alert(1)</script>")
    job.raw_text = "<img src=x onerror=alert(2)>"
    session.commit()
    html = web(session).get("/browse/label").text
    assert "<script>alert" not in html and "<img src=x" not in html
