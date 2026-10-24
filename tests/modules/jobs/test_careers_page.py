"""Follow a company from its careers page (CH-188). Saved sample pages; no network."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.careers_page import (
    allowed_address,
    find_board_in_page,
    read_careers_page,
    track_company,
)
from careerhub.modules.jobs.targets import Target, load_targets

PAGES = Path(__file__).parent / "careers_pages"
ROBOTS_OPEN = "User-agent: *\nDisallow:\n"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def client(
    pages: dict[str, str], robots: str | int = ROBOTS_OPEN, seen: list[str] | None = None
) -> httpx.Client:
    """A client that answers from `pages` (by URL) and `robots`, and records what it was asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            if isinstance(robots, int):
                return httpx.Response(robots)
            return httpx.Response(200, text=robots)
        if str(request.url) in pages:
            return httpx.Response(200, text=pages[str(request.url)])
        if request.url.host.endswith(("greenhouse.io", "lever.co", "ashbyhq.com", "lever.co")):
            return httpx.Response(404)  # a guessed board does not exist
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names resolve to a public address, so no test needs a network."""
    monkeypatch.setattr(
        "careerhub.modules.jobs.careers_page.resolve_host", lambda host: ["93.184.216.34"]
    )


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.mark.parametrize(
    ("name", "kind", "token"),
    [
        ("greenhouse_iframe.html", "greenhouse", "orchardbio"),
        ("lever_link.html", "lever", "fjordlabs"),
        ("ashby_link.html", "ashby", "granite-analytics"),
    ],
)
def test_an_embedded_board_gives_the_exact_token(name: str, kind: str, token: str) -> None:
    hit = find_board_in_page(page(name)).board
    assert hit is not None and (hit.kind, hit.token, hit.verified) == (kind, token, True)


def test_an_unsupported_system_is_named_and_a_bare_page_finds_nothing() -> None:
    assert find_board_in_page(page("workday.html")).unsupported == "Workday"
    empty = find_board_in_page(page("no_board.html"))
    assert empty.board is None and empty.unsupported is None
    assert find_board_in_page("boards.greenhouse.io/embed/").board is None  # not a board name


def test_a_found_board_becomes_a_proposed_source_and_the_target_is_followed(
    session: Session,
) -> None:
    url = "https://orchard.example/careers"
    targets: list[Target] = []
    lines = track_company(
        session, targets, "Orchard Bio", url, client({url: page("greenhouse_iframe.html")})
    )
    (target,) = targets
    assert target.enabled and (target.board_kind, target.board) == ("greenhouse", "orchardbio")
    source = session.query(Source).one()
    assert source.status == "proposed" and source.config == {"board": "orchardbio"}
    assert any("proposed" in line for line in lines)


def test_an_unsupported_page_is_recorded_on_the_target(session: Session) -> None:
    url = "https://northwind.example/careers"
    targets: list[Target] = []
    lines = track_company(session, targets, "Northwind", url, client({url: page("workday.html")}))
    assert targets[0].unsupported_ats == "Workday" and targets[0].enabled
    assert session.query(Source).count() == 0 and "Board not supported: Workday" in lines[-1]


def test_a_page_with_no_board_falls_back_to_guessing_names(session: Session) -> None:
    url = "https://quiet.example/careers"
    seen: list[str] = []
    lines = track_company(
        session, [], "Quiet Co", url, client({url: page("no_board.html")}, seen=seen),
        sleep=lambda _: None,
    )  # fmt: skip
    assert any("greenhouse.io" in u for u in seen)
    assert any("No board found" in line for line in lines)


def test_robots_txt_that_disallows_the_page_stops_the_fetch() -> None:
    url = "https://orchard.example/careers"
    seen: list[str] = []
    web = client({url: page("greenhouse_iframe.html")}, "User-agent: *\nDisallow: /careers\n", seen)
    result = read_careers_page(url, web)
    assert result.board is None and "robots.txt" in (result.problem or "")
    assert url not in seen  # the page itself was never requested


@pytest.mark.parametrize("status", [500, 503])
def test_an_unreadable_robots_txt_stops_the_fetch(status: int) -> None:
    url = "https://orchard.example/careers"
    result = read_careers_page(url, client({url: page("greenhouse_iframe.html")}, status))
    assert "robots.txt could not be read" in (result.problem or "")


def test_a_missing_robots_txt_allows_the_fetch() -> None:
    url = "https://orchard.example/careers"
    result = read_careers_page(url, client({url: page("lever_link.html")}, 404))
    assert result.board is not None and result.board.token == "fjordlabs"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/company/acme/jobs",
        "https://joinhandshake.com/x",
        "http://acme.example/careers",
        "https://127.0.0.1/careers",
        "https://localhost/careers",
    ],
)
def test_linkedin_handshake_plain_http_and_local_addresses_are_never_requested(url: str) -> None:
    seen: list[str] = []
    result = read_careers_page(url, client({}, seen=seen))
    assert result.problem and seen == [] and allowed_address(url)


def test_a_redirect_to_another_site_is_not_followed() -> None:
    url = "https://acme.example/careers"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://www.linkedin.com/jobs"})

    result = read_careers_page(url, httpx.Client(transport=httpx.MockTransport(handler)))
    assert "somewhere not allowed" in (result.problem or "")


def test_hostile_page_text_is_only_read_never_run(session: Session) -> None:
    html = "<script>alert(1)</script>" + "x" * 2_000_000 + "jobs.lever.co/late"
    assert find_board_in_page(html[:1_000_000]).board is None


def test_names_go_through_the_alias_rules_and_a_followed_company_is_not_added_twice(
    session: Session,
) -> None:
    targets = [Target(name="Name Now")]
    lines = track_company(
        session, targets, "Old Name", None, client({}), {"old name": "name now"}, lambda _: None
    )
    assert len(targets) == 1 and targets[0].enabled and "already in your list" in lines[0]


def test_the_command_adds_the_target_and_saves_it(
    session: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from careerhub import cli

    url = "https://orchard.example/careers"
    web = client({url: page("greenhouse_iframe.html")})
    monkeypatch.setattr(cli, "_with_session", lambda _s, action: action(session))
    monkeypatch.setattr(cli, "new_client", lambda: web, raising=False)
    monkeypatch.setattr("careerhub.modules.jobs.careers_page.new_client", lambda: web)
    code = main(["targets", "add", "Orchard", "Bio", "--careers-url", url], InMemoryKeyStore())
    assert code == 0 and "greenhouse" in capsys.readouterr().out
    saved = load_targets()
    assert [(t.name, t.enabled, t.board) for t in saved] == [("Orchard Bio", True, "orchardbio")]
    assert main(["targets", "add", "--careers-url"], InMemoryKeyStore()) == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://[::ffff:127.0.0.1]/",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data",
        "https://0x7f000001/",
        "https://localhost./",
        "https://foo.localhost/",
        "https://www.linkedin.com./jobs",
        "https://evil@127.0.0.1/",
    ],
)
def test_review_findings_odd_spellings_of_local_and_forbidden_addresses_are_refused(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A name like 0x7f000001 resolves to 127.0.0.1 in a real resolver.
    monkeypatch.setattr(
        "careerhub.modules.jobs.careers_page.resolve_host",
        lambda host: ["127.0.0.1"] if host.startswith("0x") else ["93.184.216.34"],
    )
    seen: list[str] = []
    result = read_careers_page(url, client({}, seen=seen))
    assert result.problem and seen == []


def test_review_findings_a_name_that_points_at_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for address in ("10.0.0.5", "192.168.1.1", "169.254.169.254", "127.0.0.1", "::1", "fd00::1"):
        monkeypatch.setattr(
            "careerhub.modules.jobs.careers_page.resolve_host", lambda host, a=address: [a]
        )
        assert allowed_address("https://jobs.example.com/") is not None, address
    monkeypatch.setattr("careerhub.modules.jobs.careers_page.resolve_host", lambda host: [])
    assert "could not be found" in (allowed_address("https://nope.example/") or "")


def routed_client(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_review_findings_a_redirect_to_plain_http_is_not_followed() -> None:
    seen: list[str] = []
    web = routed_client(
        {
            "https://acme.example/robots.txt": httpx.Response(200, text=ROBOTS_OPEN),
            "https://acme.example/jobs": httpx.Response(
                302, headers={"location": "http://acme.example/private"}
            ),
            "http://acme.example/private": httpx.Response(200, text=page("lever_link.html")),
        },
        seen,
    )
    result = read_careers_page("https://acme.example/jobs", web)
    assert result.board is None and "http://acme.example/private" not in seen


def test_review_findings_robots_txt_also_applies_to_where_a_redirect_goes() -> None:
    seen: list[str] = []
    web = routed_client(
        {
            "https://acme.example/robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /private\n"
            ),
            "https://acme.example/jobs": httpx.Response(302, headers={"location": "/private"}),
            "https://acme.example/private": httpx.Response(200, text=page("lever_link.html")),
        },
        seen,
    )
    result = read_careers_page("https://acme.example/jobs", web)
    assert result.board is None and "robots.txt" in (result.problem or "")
    assert "https://acme.example/private" not in seen


def test_review_findings_a_redirected_robots_txt_is_followed_not_ignored() -> None:
    seen: list[str] = []
    web = routed_client(
        {
            "https://acme.example/robots.txt": httpx.Response(
                301, headers={"location": "https://acme.example/real-robots.txt"}
            ),
            "https://acme.example/real-robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /jobs\n"
            ),
            "https://acme.example/jobs": httpx.Response(200, text=page("lever_link.html")),
        },
        seen,
    )
    result = read_careers_page("https://acme.example/jobs", web)
    assert result.board is None and "https://acme.example/jobs" not in seen


def test_review_findings_a_huge_page_is_cut_off_while_downloading() -> None:
    sent = {"bytes": 0}

    def body() -> Iterator[bytes]:
        for _ in range(10_000):  # up to 10 GB if anyone keeps reading
            sent["bytes"] += 1_000_000
            yield b"x" * 1_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body())

    web = httpx.Client(transport=httpx.MockTransport(handler))
    result = read_careers_page("https://acme.example/jobs", web)
    assert result.board is None and sent["bytes"] <= 5_000_000
