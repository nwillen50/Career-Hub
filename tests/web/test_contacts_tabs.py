"""The Contacts and Interactions tabs (CH-171). Saved sample data only."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_tracker_page import FORM, ORIGIN, PORT, TOKEN

from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Interaction, Person, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.web.app import create_app


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def post(c: TestClient, path: str, body: str) -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/contacts").text)
    assert token
    return c.post(path, headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}&{body}")


@pytest.fixture
def ada(session: Session) -> Person:
    person = Person(
        full_name="Ada Example",
        title="Engineer",
        company=Company(name="Acme", name_key="acme"),
        how_known="former manager; alumni: Tufts Jumbos",
    )
    session.add(person)
    session.commit()
    return person


def test_both_tabs_render_accessibly_with_and_without_data(session: Session, ada: Person) -> None:
    c = web(session)
    for path in ("/contacts", "/interactions"):
        html = c.get(path).text
        assert check_page(html) == [] and f'href="{path}" aria-current="page"' in html
    assert "Ada Example" in c.get("/contacts").text
    assert (
        "former manager" in c.get("/contacts").text
        and "alumni: Tufts Jumbos" in c.get("/contacts").text
    )


def test_contacts_can_be_filtered_by_name_or_company(session: Session, ada: Person) -> None:
    session.add(Person(full_name="Bo Other"))
    session.commit()
    c = web(session)
    html = c.get("/contacts?q=acme").text
    assert "Ada Example" in html and "Bo Other" not in html
    assert "Nobody matches that." in c.get("/contacts?q=zzz").text


def test_editing_how_you_know_someone_keeps_the_alumni_tag(session: Session, ada: Person) -> None:
    c = web(session)
    reply = post(c, f"/people/{ada.id}", "how_known=" + quote("my old mentor"))
    assert reply.status_code == 303 and reply.headers["location"] == "/contacts"
    session.expire_all()
    assert session.get(Person, ada.id).how_known == "my old mentor; alumni: Tufts Jumbos"  # type: ignore[union-attr]


def test_a_too_long_edit_is_refused_and_nothing_changes(session: Session, ada: Person) -> None:
    reply = post(web(session), f"/people/{ada.id}", "how_known=" + "x" * 600)
    assert reply.status_code == 200 and "too long" in reply.text
    session.expire_all()
    assert session.get(Person, ada.id).how_known == "former manager; alumni: Tufts Jumbos"  # type: ignore[union-attr]


def test_logging_a_contact_with_a_follow_up_shows_it_in_the_history(
    session: Session, ada: Person
) -> None:
    c = web(session)
    day = (utcnow() + timedelta(days=5)).date().isoformat()
    reply = post(
        c, "/interactions", f"person={ada.id}&channel=email&summary=Thanks+note&follow_up={day}"
    )
    assert reply.status_code == 303
    html = c.get("/interactions").text
    assert "Thanks note" in html and day in html and "Email" in html
    assert check_page(html) == []


def test_bad_input_re_shows_the_form_with_a_message(session: Session, ada: Person) -> None:
    c = web(session)
    assert (
        "not understood"
        in post(c, "/interactions", f"person={ada.id}&channel=email&follow_up=tomorrow").text
    )
    assert "Choose who" in post(c, "/interactions", "person=abc&channel=email").text
    assert "channel" in post(c, "/interactions", f"person={ada.id}&channel=pigeon").text
    assert "gone" in post(c, "/interactions", "person=99999&channel=email").text
    assert session.query(Interaction).count() == 0


def test_done_clears_the_reminder_and_can_be_reopened(session: Session, ada: Person) -> None:
    now = utcnow()
    row = Interaction(
        person_id=ada.id, occurred_at=now - timedelta(days=9), channel="call",
        follow_up_due=now - timedelta(days=1),
    )  # fmt: skip
    session.add(row)
    session.commit()
    c = web(session)
    assert 'class="overdue-note">due' in c.get("/interactions").text
    assert post(c, f"/interactions/{row.id}/done", "done=1").status_code == 303
    html = c.get("/interactions").text
    assert "overdue-note" not in html and "Reopen" in html
    session.expire_all()
    assert session.get(Interaction, row.id).follow_up_done is True  # type: ignore[union-attr]
    post(c, f"/interactions/{row.id}/done", "done=0")
    session.expire_all()
    assert session.get(Interaction, row.id).follow_up_done is False  # type: ignore[union-attr]


def test_done_on_something_without_a_follow_up_says_so(session: Session, ada: Person) -> None:
    row = Interaction(person_id=ada.id, occurred_at=utcnow(), channel="call")
    session.add(row)
    session.commit()
    assert "no follow-up" in post(web(session), f"/interactions/{row.id}/done", "done=1").text


def test_a_post_without_the_csrf_token_is_refused(session: Session, ada: Person) -> None:
    c = web(session)
    reply = c.post(f"/people/{ada.id}", headers={**ORIGIN, **FORM}, content="how_known=x")
    assert reply.status_code == 403


def test_a_follow_up_for_today_is_accepted(session: Session, ada: Person) -> None:
    today = utcnow().date().isoformat()
    reply = post(web(session), "/interactions", f"person={ada.id}&channel=call&follow_up={today}")
    assert reply.status_code == 303 and session.query(Interaction).count() == 1


def test_a_typed_wildcard_matches_itself_not_everyone(session: Session, ada: Person) -> None:
    session.add(Person(full_name="100% Bo"))
    session.commit()
    c = web(session)
    assert "1 person." in c.get("/contacts?q=%25").text
    assert "Nobody matches that." in c.get("/contacts?q=_").text


def test_email_and_phone_show_edit_and_clear_in_place(session: Session, ada: Person) -> None:
    """CH-196: editable columns; the email is a mailto link; nothing is sent by Career Hub."""
    c = web(session)
    reply = post(
        c, f"/people/{ada.id}",
        "how_known=" + quote("former manager") + "&email=" + quote("ada@example.org")
        + "&phone=" + quote("+1 (617) 555-0100"),
    )  # fmt: skip
    assert reply.status_code == 303
    session.expire_all()
    person = session.get(Person, ada.id)
    assert person is not None and (person.email, person.phone) == (
        "ada@example.org", "+1 (617) 555-0100",
    )  # fmt: skip
    html = c.get("/contacts").text
    assert 'value="ada@example.org"' in html and 'value="+1 (617) 555-0100"' in html
    assert 'href="mailto:ada%40example.org"' in html and check_page(html) == []
    post(c, f"/people/{ada.id}", "how_known=x&email=&phone=")
    session.expire_all()
    assert (person.email, person.phone) == (None, None)


def test_bad_email_or_phone_is_refused_and_nothing_changes(session: Session, ada: Person) -> None:
    c = web(session)
    for body in (
        "email=not-an-email",
        "email=a%40b.c%22%3E%3Cscript%3E",
        "phone=call+me",
        "phone=1" * 1 + "2" * 60,
    ):
        reply = post(c, f"/people/{ada.id}", "how_known=changed&" + body)
        assert reply.status_code == 200 and "alert" in reply.text
    session.expire_all()
    assert session.get(Person, ada.id).how_known == "former manager; alumni: Tufts Jumbos"  # type: ignore[union-attr]


def test_hostile_email_text_is_escaped_in_the_page(session: Session) -> None:
    session.add(Person(full_name="Eve", email='x"><script>alert(1)</script>@e.co'))
    session.commit()
    html = web(session).get("/contacts").text
    assert "<script>alert" not in html


def test_email_and_phone_never_reach_a_prompt_or_the_logs(
    session: Session, ada: Person, caplog: pytest.LogCaptureFixture
) -> None:
    from careerhub.modules.conversation import build_context
    from careerhub.modules.network.interactions import set_contact_details

    caplog.set_level("DEBUG")
    set_contact_details(session, ada.id, "ada@example.org", "617 555 0100")
    context = build_context(session, None, "Nicholas", [], None)
    web(session).get("/contacts")
    assert "ada@example.org" not in context and "617 555 0100" not in context
    assert "ada@example.org" not in caplog.text and "555 0100" not in caplog.text


def test_review_findings_a_failed_how_known_save_also_leaves_email_and_phone_alone(
    session: Session, ada: Person
) -> None:
    reply = post(
        web(session), f"/people/{ada.id}",
        "how_known=" + "x" * 600 + "&email=" + quote("new@example.org")
        + "&phone=" + quote("617 555 0100"),
    )  # fmt: skip
    assert reply.status_code == 200 and "too long" in reply.text
    session.expire_all()
    person = session.get(Person, ada.id)
    assert person is not None and (person.email, person.phone) == (None, None)


def test_review_findings_a_request_naming_only_one_of_email_or_phone_keeps_the_other(
    session: Session, ada: Person
) -> None:
    ada.phone = "617 555 0100"
    session.commit()
    post(web(session), f"/people/{ada.id}", "how_known=x&email=" + quote("ada@example.org"))
    session.expire_all()
    person = session.get(Person, ada.id)
    assert person is not None and (person.email, person.phone) == (
        "ada@example.org",
        "617 555 0100",
    )


def test_copy_buttons_are_per_value_labelled_and_hidden_without_javascript(
    session: Session,
) -> None:
    """CH-204: each email and phone has its own copy button, shown only for that cell."""
    session.add(Person(full_name="Maya Chen", email="maya@example.com", phone="555-0100"))
    session.commit()
    c = web(session)
    html = c.get("/contacts").text
    assert check_page(html) == []
    assert 'aria-label="Copy Maya Chen\'s email"' in html.replace("&#39;", "'")
    assert 'aria-label="Copy Maya Chen\'s phone"' in html.replace("&#39;", "'")
    assert html.count("data-copy-from=") == 2
    assert html.count('class="copy-btn" hidden') == 2  # hidden until the script runs
    assert html.count('role="status"') >= 2  # 'Copied' is announced
    assert "mailto:" in html  # Write stays; there is no tel: link
    assert "tel:" not in html
    css = c.get("/static/app.css").text
    assert ".copy-cell:hover .copy-btn" in css and ".copy-cell:focus-within .copy-btn" in css
    assert ".copy-btn { opacity: 0" in css and "tr:hover .copy" not in css  # per cell, not per row


def test_the_copy_script_is_bundled_and_uses_the_clipboard_only(session: Session) -> None:
    script = web(session).get("/static/copy.js").text
    assert "navigator.clipboard.writeText" in script
    assert "http://" not in script and "https://" not in script and "fetch(" not in script
    assert "2000" in script and "Copied" in script
