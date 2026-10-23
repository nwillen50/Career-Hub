"""The local web app: security, layout, and tabs (CH-138, ADR-0043).

`hub web` starts this on 127.0.0.1 only. Nothing here reaches the internet: HTMX, the stylesheets,
and the fonts are served from this package, and a strict Content-Security-Policy makes the browser
refuse anything else.

Who may talk to the app:

- **Host check.** Only `127.0.0.1` and `localhost` (on the app's own port) are answered; any other
  Host header gets 400. This stops DNS-rebinding pages from reading the app.
- **Session token.** Each start makes a new random token. Opening the link `hub web` prints
  (`/?t=<token>`) sets a session cookie (HttpOnly, SameSite=Strict); without it every page says how
  to open the app.
- **Origin and CSRF.** Any request that changes something must come from the app's own origin and
  carry the session's CSRF token (header `X-CSRF-Token`, which HTMX sends, or form field `csrf`).

Everything shown is escaped by Jinja2's autoescape; templates never mark posting or profile text
as safe.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from careerhub.core.migrate import migrate
from careerhub.core.secrets import KeychainKeyStore, KeyStore
from careerhub.core.settings import SettingsError
from careerhub.core.storage import StorageError, open_database
from careerhub.profile.files import ProfileFileError

SESSION_COOKIE = "ch_session"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_FORM_BYTES = 64 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
#: The tabs, in order: (path, label).
TABS = (
    ("/today", "Today"),
    ("/plan", "Plan"),
    ("/tracker", "Tracker"),
    ("/status", "System status"),
)
PAGES = {
    "/today": ("Today", "Your top jobs, follow-ups, and deadlines will appear here."),
    "/plan": ("Plan", "This week's suggested blocks will appear here."),
    "/tracker": ("Tracker", "Applications and their statuses will appear here."),
    "/status": ("System status", "Runs, sources, models, and spending will appear here."),
}


def database_opens(store: KeyStore | None = None) -> bool:
    """Say whether the encrypted database can be opened (key in the Keychain, file intact)."""
    try:
        migrate(open_database(store or KeychainKeyStore()))
    except Exception:  # any failure means "can't open"; the page never says why
        return False
    return True


def create_app(
    token: str,
    port: int,
    check_database: Callable[[], bool] = database_opens,
) -> FastAPI:
    """Build the app. `token` is this start's secret; `check_database` says if data is usable."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(
        directory=str(resources.files("careerhub.web").joinpath("templates"))
    )
    sessions: dict[str, str] = {}  # session id -> CSRF token
    opened = {"ok": False}  # once the database has opened, it is not re-checked on every page
    static_dir = str(resources.files("careerhub.web").joinpath("static"))
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def page(request: Request, status: int, **context: Any) -> HTMLResponse:
        csrf = sessions.get(request.cookies.get(SESSION_COOKIE, ""), "")
        base = {"tabs": TABS, "current": request.url.path, "csrf": csrf}
        return templates.TemplateResponse(
            request, "page.html", {**base, **context}, status_code=status
        )

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = request.headers.get("host", "")
        name, _, given_port = host.partition(":")
        if name not in ALLOWED_HOSTS or (given_port and given_port != str(port)):
            return HTMLResponse("Bad host.", status_code=400)
        path = request.url.path
        response: Response
        if path.startswith("/static/"):
            response = await call_next(request)
        elif path == "/" and "t" in request.query_params:
            response = _start_session(request, token, sessions)
        else:
            sid = request.cookies.get(SESSION_COOKIE, "")
            if sid not in sessions:
                response = page(
                    request,
                    403,
                    title="Open Career Hub with its link",
                    message="Open Career Hub with the link `hub web` printed in your terminal.",
                    heading="Open Career Hub with its link",
                    plain=True,
                )
            elif request.method in UNSAFE_METHODS and not await _request_is_safe(
                request, host, sessions[sid]
            ):
                response = page(
                    request,
                    403,
                    title="Request refused",
                    heading="Request refused",
                    message="That request did not come from this page. Reload and try again.",
                    plain=True,
                )
            else:
                response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return RedirectResponse("/today", status_code=303)

    def tab(path: str) -> Callable[[Request], Response]:
        title, empty = PAGES[path]

        def render(request: Request) -> Response:
            opened["ok"] = opened["ok"] or check_database()
            if not opened["ok"]:
                return page(
                    request,
                    503,
                    title=title,
                    heading="Career Hub can't open its data",
                    message=(
                        "The encrypted database could not be opened. Check that the Keychain item "
                        "is there and that the data folder is intact, then reload. Nothing was "
                        "changed."
                    ),
                    error=True,
                )
            return page(request, 200, title=title, heading=title, message=empty, empty=True)

        return render

    for path in PAGES:
        app.add_api_route(path, tab(path), methods=["GET"], response_class=HTMLResponse)

    @app.post("/session/end")
    def end_session(request: Request) -> Response:
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = page(
            request,
            200,
            title="Session ended",
            heading="Session ended",
            message="Open the link `hub web` printed to start again.",
            plain=True,
        )
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.exception_handler(SettingsError)
    @app.exception_handler(ProfileFileError)
    def settings_problem(request: Request, error: Exception) -> Response:
        """Show a designed error page, not a stack trace, for a broken settings or profile file."""
        return page(
            request,
            503,
            title="A settings file has a problem",
            heading="A settings file has a problem",
            message=(
                "Career Hub could not read settings.json, preferences.yaml, or profile.md. "
                "Run `hub profile show` in the terminal to see which line to fix, then reload. "
                "Nothing was changed."
            ),
            error=True,
        )

    return app


def _start_session(request: Request, token: str, sessions: dict[str, str]) -> Response:
    """Check the one-time-per-start token; set the session cookie and go to Today."""
    given = request.query_params.get("t", "")
    if not hmac.compare_digest(given.encode(), token.encode()):
        return HTMLResponse("That link is not valid for this run of Career Hub.", status_code=403)
    sid = secrets.token_urlsafe(32)
    sessions[sid] = secrets.token_urlsafe(32)
    response = RedirectResponse("/today", status_code=303)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", path="/")
    return response


async def _request_is_safe(request: Request, host: str, csrf: str) -> bool:
    """Check Origin and the CSRF token of a request that changes something."""
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != host:
        return False
    if origin is None and request.headers.get("sec-fetch-site", "same-origin") not in (
        "same-origin",
        "none",
    ):
        return False
    given = request.headers.get("x-csrf-token", "")
    if not given and request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        body = await request.body()
        if len(body) <= MAX_FORM_BYTES:
            given = parse_qs(body.decode("utf-8", "replace")).get("csrf", [""])[0]
    return bool(given) and hmac.compare_digest(given.encode(), csrf.encode())


__all__ = ["StorageError", "create_app", "database_opens"]
