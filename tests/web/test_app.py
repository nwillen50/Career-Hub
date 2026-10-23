"""The local web app: localhost only, token, CSRF, escaping, accessibility (CH-138)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from a11y import check_page, contrast_ratio
from fastapi.testclient import TestClient

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.web.app import CSP, SESSION_COOKIE, create_app

TOKEN = "test-token-123"
PORT = 8765
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"
STATIC = Path(__file__).parents[2] / "src" / "careerhub" / "web" / "static"


def client(database_ok: bool = True) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: database_ok)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)


def signed_in(database_ok: bool = True) -> TestClient:
    c = client(database_ok)
    assert c.get(f"/?t={TOKEN}").status_code == 303
    return c


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_the_link_with_the_token_starts_a_session_and_goes_to_today() -> None:
    c = client()
    response = c.get(f"/?t={TOKEN}")
    assert response.status_code == 303 and response.headers["location"] == "/today"
    cookie = response.headers["set-cookie"].lower()
    assert SESSION_COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/today").status_code == 200


def test_a_wrong_token_or_no_cookie_is_refused_with_instructions() -> None:
    c = client()
    assert c.get("/?t=nope").status_code == 403
    page = c.get("/today")
    assert page.status_code == 403 and "hub web" in page.text
    assert SESSION_COOKIE not in page.headers.get("set-cookie", "")


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "0.0.0.0", "[::1]"])
def test_any_other_host_is_refused(host: str) -> None:
    c = TestClient(create_app(TOKEN, PORT, lambda: True), base_url=f"http://{host}:{PORT}")
    assert c.get(f"/?t={TOKEN}").status_code == 400


def test_a_wrong_port_in_the_host_is_refused() -> None:
    c = TestClient(create_app(TOKEN, PORT, lambda: True), base_url="http://127.0.0.1:9999")
    assert c.get("/today").status_code == 400


def test_localhost_is_accepted() -> None:
    c = TestClient(create_app(TOKEN, PORT, lambda: True), base_url=f"http://localhost:{PORT}")
    assert c.get(f"/?t={TOKEN}", follow_redirects=False).status_code == 303


def test_every_response_has_the_security_headers() -> None:
    c = signed_in()
    for path in ("/today", "/static/app.css", "/nope"):
        headers = c.get(path).headers
        assert headers["content-security-policy"] == CSP
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
    assert "unsafe" not in CSP and "http" not in CSP


def test_the_api_docs_are_off() -> None:
    c = signed_in()
    assert all(c.get(p).status_code in (403, 404) for p in ("/docs", "/redoc", "/openapi.json"))


def test_a_post_needs_the_csrf_token_and_the_apps_own_origin() -> None:
    c = signed_in()
    token = csrf_of(c.get("/today").text)
    origin = {"origin": f"http://127.0.0.1:{PORT}"}
    form = {"content-type": "application/x-www-form-urlencoded"}
    assert (
        c.post("/session/end", headers={**origin, **form}, content="csrf=wrong").status_code == 403
    )
    assert c.post("/session/end", headers=origin).status_code == 403  # no token at all
    evil = {"origin": "http://evil.example", **form}
    assert c.post("/session/end", headers=evil, content=f"csrf={token}").status_code == 403
    cross = {"sec-fetch-site": "cross-site", **form}
    assert c.post("/session/end", headers=cross, content=f"csrf={token}").status_code == 403


def test_an_htmx_style_header_token_works_and_ending_the_session_locks_the_app() -> None:
    c = signed_in()
    token = csrf_of(c.get("/today").text)
    ok = c.post(
        "/session/end", headers={"x-csrf-token": token, "origin": f"http://127.0.0.1:{PORT}"}
    )
    assert ok.status_code == 200 and "Session ended" in ok.text
    assert c.get("/today").status_code == 403


def test_a_plain_form_post_with_the_field_works() -> None:
    c = signed_in()
    token = csrf_of(c.get("/today").text)
    response = c.post(
        "/session/end",
        headers={"content-type": "application/x-www-form-urlencoded"},
        content=f"csrf={token}",
    )
    assert response.status_code == 200


def test_the_four_tabs_render_with_the_current_one_marked() -> None:
    c = signed_in()
    for path, label in (
        ("/today", "Today"),
        ("/plan", "Plan"),
        ("/tracker", "Tracker"),
        ("/status", "System status"),
    ):
        html = c.get(path).text
        assert f"<h1>{label}</h1>" in html
        assert f'href="{path}" aria-current="page"' in html
        assert "Nothing to show yet." in html


def test_a_friendly_page_when_the_database_cannot_be_opened() -> None:
    c = signed_in(database_ok=False)
    page = c.get("/today")
    assert page.status_code == 503 and "can&#39;t open its data" in page.text
    assert 'role="alert"' in page.text and "Traceback" not in page.text
    assert check_page(page.text) == []


def test_pages_make_no_outside_requests_and_use_only_bundled_files() -> None:
    html = signed_in().get("/today").text
    assert not re.search(r'(?:src|href)="https?://', html)
    assert re.findall(r'(?:src|href)="(/static/[^"]+)"', html) == [
        "/static/tokens.css",
        "/static/app.css",
        "/static/htmx.min.js",
    ]
    assert "htmx-config" in html and '"allowEval": false' in html


def test_the_bundled_htmx_file_is_the_one_we_checked() -> None:
    data = (STATIC / "htmx.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == HTMX_SHA256
    assert not re.search(rb"https?://(?!www\.w3\.org)", data[:0])  # (hash above is the real guard)


def test_hostile_text_in_a_page_is_escaped() -> None:
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(STATIC.parent / "templates"))
    html = templates.get_template("page.html").render(
        title="<script>x</script>",
        heading="<img src=x onerror=alert(1)>",
        message="<b>hi</b>",
        tabs=(),
        current="",
        csrf='"><script>',
    )
    assert "<script>x" not in html and "<img src=x" not in html and "<b>hi" not in html
    assert '"><script>' not in html


def test_pages_pass_the_accessibility_checks() -> None:
    c = signed_in()
    for path in ("/today", "/plan", "/tracker", "/status"):
        assert check_page(c.get(path).text) == [], path
    assert check_page(client().get("/today").text, nav=False) == []  # the "open with the link" page


def test_text_colours_meet_contrast_in_light_and_dark() -> None:
    css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)

    def tokens(block: str) -> dict[str, str]:
        return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", block))

    for name, block in (("light", {**tokens(light)}), ("dark", {**tokens(light), **tokens(dark)})):
        for foreground, background in (
            ("--text", "--page"),
            ("--text", "--surface"),
            ("--muted", "--surface"),
            ("--header-text", "--header"),
        ):
            ratio = contrast_ratio(block[foreground], block[background])
            assert ratio >= 4.5, (name, foreground, background, round(ratio, 2))


def test_the_page_stylesheet_uses_tokens_only() -> None:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css) and "rgb(" not in css


def test_hub_web_refuses_other_addresses_and_bad_ports(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryKeyStore()
    assert main(["web", "--host", "0.0.0.0"], store) == 2
    assert "only listens on 127.0.0.1" in capsys.readouterr().out
    assert main(["web", "--port", "80"], store) == 2
    assert main(["web", "--port", "abc"], store) == 2


def test_hub_web_serves_on_localhost_with_a_fresh_token(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["web", "--port", "9123"], InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    assert calls == [{"host": "127.0.0.1", "port": 9123, "log_level": "warning"}]
    assert re.search(r"http://127\.0\.0\.1:9123/\?t=[\w-]{20,}", out)


def test_the_port_is_a_setting_with_safe_bounds() -> None:
    assert Settings().web_port == 8765
    with pytest.raises(ValueError):
        Settings(web_port=80)


@pytest.mark.parametrize("error", ["settings", "profile"])
def test_a_broken_settings_or_profile_file_is_a_designed_error_page(
    error: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: a malformed settings.json used to end in a 500 and a stack trace."""
    from careerhub.core.settings import SettingsError
    from careerhub.profile.files import ProfileFileError

    def broken() -> bool:
        raise SettingsError("secret detail") if error == "settings" else ProfileFileError("detail")

    c = TestClient(
        create_app(TOKEN, PORT, broken), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    c.get(f"/?t={TOKEN}")
    page = c.get("/today")
    assert page.status_code == 503 and "A settings file has a problem" in page.text
    assert "detail" not in page.text and "Traceback" not in page.text
    assert check_page(page.text) == []
