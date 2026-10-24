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
from datetime import datetime
from importlib import resources
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Application, Job, utcnow
from careerhub.core.secrets import KeychainKeyStore, KeyStore
from careerhub.core.settings import SettingsError, load_settings
from careerhub.core.storage import StorageError, open_database, session_factory
from careerhub.llm import Router
from careerhub.llm.catalog import ollama_is_running, with_catalog
from careerhub.llm.setup import NoRoute, build_router, routes_for
from careerhub.modules.conversation import ask, build_context, recent_messages
from careerhub.modules.jobs.applications import (
    TrackerError,
    set_follow_up,
    set_next_step,
)
from careerhub.modules.jobs.applications import track as change_status
from careerhub.modules.jobs.reactions import (
    UNDO_SECONDS,
    ReactionError,
    react,
    reacted_job_ids,
    undo,
)
from careerhub.modules.network import UnknownPerson
from careerhub.modules.network.interactions import (
    CHANNELS,
    InteractionError,
    log_interaction,
    set_follow_up_done,
    set_own_words,
)
from careerhub.modules.proposals import ProposalError, declined_proposals, pending_proposals
from careerhub.modules.proposals import accept as accept_proposal
from careerhub.modules.proposals import reject as reject_proposal
from careerhub.modules.proposals import reopen as reopen_proposal
from careerhub.profile.facts import UnknownExtraction, decide
from careerhub.profile.files import ProfileFileError, load_profile_doc
from careerhub.profile.rubric import load_rubric
from careerhub.web.browse_view import (
    AGES,
    MODES,
    REACTED,
    SORTS,
    BrowseQuery,
    gather_browse,
)
from careerhub.web.grouping import group_by_place
from careerhub.web.job_detail import gather_job
from careerhub.web.people_view import gather_contacts, gather_interactions, people_choices
from careerhub.web.plan_view import gather_plan
from careerhub.web.profile_view import gather_profile
from careerhub.web.status_view import gather_status
from careerhub.web.today import SECTIONS, clean_section, gather_today, job_cards
from careerhub.web.tracker_view import (
    STATUS_CHOICES,
    VIEW_CHOICES,
    TrackerQuery,
    clean_query,
    csv_for,
    follow_up_from_text,
    gather_tracker,
)

#: A job id the database can hold (SQLite integers are 64-bit); anything else is a 422, not a 500.
JobId = Annotated[int, Path(ge=1, le=2**63 - 1)]
SESSION_COOKIE = "ch_session"
SECTION_COOKIE = "ch_today_section"
REACTION_LABELS = {
    "interested": "interested",
    "not_interested": "not interested",
    "applied": "applied",
}
REASON_LABELS = (
    ("level", "Wrong level"),
    ("location", "Wrong location"),
    ("field", "Wrong field"),
    ("company", "Not this company"),
    ("pay", "Pay"),
)
SECTION_LABELS = (
    ("all", "All"),
    ("top", "Top jobs"),
    ("exploration", "Exploration"),
    ("followups", "Follow-ups"),
    ("deadlines", "Deadlines"),
)
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
    ("/browse", "Browse"),
    ("/plan", "Plan"),
    ("/tracker", "Tracker"),
    ("/contacts", "Contacts"),
    ("/interactions", "Interactions"),
    ("/profile", "Profile"),
    ("/status", "System status"),
)
PAGES = {
    "/today": ("Today", "Your top jobs, follow-ups, and deadlines will appear here."),
    "/browse": ("Browse", "Every open job will appear here."),
    "/plan": ("Plan", "This week's suggested blocks will appear here."),
    "/tracker": ("Tracker", "Applications and their statuses will appear here."),
    "/contacts": ("Contacts", "The people you know will appear here."),
    "/interactions": ("Interactions", "Your contact log and follow-ups will appear here."),
    "/profile": ("Profile", "What Career Hub knows about you will appear here."),
    "/status": ("System status", "Runs, sources, models, and spending will appear here."),
}


def database_opens(store: KeyStore | None = None) -> bool:
    """Say whether the encrypted database can be opened (key in the Keychain, file intact)."""
    try:
        migrate(open_database(store or KeychainKeyStore()))
    except Exception:  # any failure means "can't open"; the page never says why
        return False
    return True


def make_session_opener(store: KeyStore | None = None) -> Callable[[], Session]:
    """Return a function that opens a database session, opening the encrypted database on first use.

    Each session gets its own connection and releases it, so any worker thread is safe.
    """
    holder: dict[str, Engine] = {}

    def open_session() -> Session:
        if "engine" not in holder:
            holder["engine"] = open_database(
                store or KeychainKeyStore(), one_connection_per_use=True
            )
        return session_factory(holder["engine"])()

    return open_session


def make_router_opener(
    store: KeyStore | None = None,
) -> Callable[[], tuple[Router, list[str]]]:
    """Return a function that builds the model router and the providers to try for "chat".

    Raises `NoRoute` (from `routes_for`) when no usable model is set up for chat.
    """

    def open_router() -> tuple[Router, list[str]]:
        keys = store or KeychainKeyStore()
        sessions = session_factory(open_database(keys, one_connection_per_use=True))
        settings = with_catalog(load_settings(), keys, ollama_is_running).settings
        setup = build_router(settings, keys, sessions)
        return setup.router, routes_for(settings, "chat", setup.built)

    return open_router


def create_app(
    token: str,
    port: int,
    check_database: Callable[[], bool] = database_opens,
    open_session: Callable[[], Session] | None = None,
    key_store: KeyStore | None = None,
    local_running: Callable[[], bool] = ollama_is_running,
    open_router: Callable[[], tuple[Router, list[str]]] | None = None,
) -> FastAPI:
    """Build the app. `token` is this start's secret; `check_database` says if data is usable.

    `open_session` gives a database session for the pages that read data (None: those pages show
    their empty state, which is what the skeleton tests use).
    """
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
        if path not in (
            "/today",
            "/browse",
            "/plan",
            "/status",
            "/tracker",
            "/contacts",
            "/interactions",
            "/profile",
        ):
            app.add_api_route(path, tab(path), methods=["GET"], response_class=HTMLResponse)

    @app.get("/today", response_class=HTMLResponse)
    def today(request: Request, section: str | None = None, group: str | None = None) -> Response:
        if open_session is None:
            return tab("/today")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/today")(request)
        chosen = clean_section(section or request.cookies.get(SECTION_COOKIE))
        with open_session() as session:
            data = gather_today(session)
        shortlist = data.shortlist
        cards = job_cards(data.jobs)
        by_place = group == "place"
        response = templates.TemplateResponse(
            request,
            "today.html",
            {
                "tabs": TABS,
                "current": "/today",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "Today",
                "heading": f"Today, {data.local_now or data.now:%a %b %d}",
                "built": f"{data.local_now or data.now:%a %b %d, %H:%M}",
                "last_run": f"{shortlist.last_success:%a %b %d, %H:%M}"
                if shortlist.last_success
                else None,
                "last_error": shortlist.last_error,
                "waiting_for": shortlist.waiting_for,
                "reasons": REASON_LABELS,
                "sections": SECTION_LABELS,
                "section": chosen,
                "jobs": cards,
                "by_place": by_place,
                "place_groups": group_by_place([c.entry.location for c in cards])
                if by_place
                else [],
                "follow_ups": data.follow_ups,
                "more_overdue": data.more_overdue,
                "deadlines": data.deadlines,
            },
        )
        if section in SECTIONS:  # remember the choice between visits
            response.set_cookie(SECTION_COOKIE, chosen, samesite="strict", path="/")
        return response

    @app.get("/browse", response_class=HTMLResponse)
    def browse_tab(request: Request) -> Response:
        if open_session is None:
            return tab("/browse")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/browse")(request)
        query = BrowseQuery.clean(dict(request.query_params))
        with open_session() as session:
            view = gather_browse(session, query)
        return templates.TemplateResponse(
            request,
            "browse.html",
            {
                "tabs": TABS,
                "current": "/browse",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "Browse",
                "heading": "Browse",
                "view": view,
                "sorts": SORTS,
                "modes": MODES,
                "ages": AGES,
                "reacted_options": REACTED,
                "reasons": REASON_LABELS,
                "place_groups": group_by_place([c.entry.location for c in view.cards])
                if view.query.group
                else [],
            },
        )

    @app.get("/plan", response_class=HTMLResponse)
    def plan_tab(request: Request) -> Response:
        if open_session is None:
            return tab("/plan")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/plan")(request)
        with open_session() as session:
            view = gather_plan(session)
        return templates.TemplateResponse(
            request,
            "plan.html",
            {
                "tabs": TABS,
                "current": "/plan",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "Plan",
                "heading": "Plan",
                "plan": view,
            },
        )

    @app.get("/profile", response_class=HTMLResponse)
    def profile_tab(request: Request) -> Response:
        if open_session is None:
            return tab("/profile")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/profile")(request)
        with open_session() as session:
            view = gather_profile(session)
        return templates.TemplateResponse(
            request,
            "profile.html",
            {
                "tabs": TABS,
                "current": "/profile",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "Profile",
                "heading": "Profile",
                "profile": view,
            },
        )

    @app.post("/profile/facts/{extraction_id}/{decision}")
    def decide_facts(request: Request, extraction_id: JobId, decision: str) -> Response:
        if open_session is None or decision not in ("accept", "reject"):
            return HTMLResponse("Not found.", status_code=404)
        with open_session() as session:
            try:
                decide(session, extraction_id, decision == "accept")
            except UnknownExtraction:
                pass  # already decided in another tab; the page shows what is left
        return RedirectResponse("/profile", status_code=303)

    @app.get("/status", response_class=HTMLResponse)
    def status_tab(request: Request) -> Response:
        if open_session is None:
            return tab("/status")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/status")(request)
        with open_session() as session:
            view = gather_status(session, key_store, local_running=local_running)
        return templates.TemplateResponse(
            request,
            "status.html",
            {
                "tabs": TABS,
                "current": "/status",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "System status",
                "heading": "System status",
                "status": view,
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: JobId) -> Response:
        if open_session is None:
            return tab("/today")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/today")(request)
        with open_session() as session:
            detail = gather_job(session, job_id)
        if detail is None:
            return page(
                request,
                404,
                title="No such job",
                heading="No such job",
                message="That job is not in your list. It may have been removed.",
            )
        return templates.TemplateResponse(
            request,
            "job.html",
            {
                "tabs": TABS,
                "current": "/today",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": detail.title,
                "heading": detail.title,
                "job": detail,
                "reasons": REASON_LABELS,
            },
        )

    def reaction_page(request: Request, name: str, **context: Any) -> Response:
        """Answer a reaction: a small card fragment for HTMX, or back to Today for a plain form."""
        csrf = sessions.get(request.cookies.get(SESSION_COOKIE, ""), "")
        if request.headers.get("hx-request") != "true":
            return RedirectResponse("/today", status_code=303)
        return templates.TemplateResponse(request, name, {"csrf": csrf, **context})

    @app.post("/jobs/{job_id}/react")
    async def react_to_job(request: Request, job_id: JobId, reason: str | None = None) -> Response:
        if open_session is None:
            return HTMLResponse("No data to react to.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        reaction = form.get("reaction", [""])[0]
        with open_session() as session:
            try:
                saved = react(session, job_id, reaction, reason)
            except ReactionError as error:
                return reaction_page(request, "note.html", message=str(error))
            feedback_id = saved.id
        return reaction_page(
            request,
            "reacted.html",
            job_id=job_id,
            feedback_id=feedback_id,
            label=REACTION_LABELS.get(reaction, reaction),
            reason_label=dict(REASON_LABELS).get(reason or ""),
            seconds=UNDO_SECONDS,
        )

    @app.post("/jobs/{job_id}/undo")
    async def undo_reaction(request: Request, job_id: JobId) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        given = form.get("feedback_id", [""])[0]
        feedback_id = int(given) if given.isdigit() else None
        with open_session() as session:
            taken_back = undo(session, job_id, feedback_id)
            still_hidden = job_id in reacted_job_ids(session)
        if not taken_back:
            message = "It is too late to undo that."
        elif still_hidden:
            message = "Reaction undone. An earlier reaction still keeps this job off Today."
        else:
            message = "Reaction undone. The job is back on Today after a reload."
        return reaction_page(request, "note.html", message=message)

    def tracker_context(request: Request, **extra: Any) -> dict[str, Any]:
        return {
            "tabs": TABS,
            "current": "/tracker",
            "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
            "status_choices": STATUS_CHOICES,
            **extra,
        }

    @app.get("/tracker", response_class=HTMLResponse)
    def tracker_tab(
        request: Request,
        view: str | None = None,
        q: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
        sort: str | None = None,
        dir: str | None = None,  # noqa: A002  (the URL parameter is called dir)
        group: str | None = None,
    ) -> Response:
        if open_session is None:
            return tab("/tracker")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/tracker")(request)
        query = clean_query(view, q, status or [], sort, dir, group)
        with open_session() as session:
            rows = gather_tracker(session, query, utcnow())
        return templates.TemplateResponse(
            request,
            "tracker.html",
            tracker_context(
                request,
                title="Tracker",
                heading="Tracker",
                query=query,
                rows=rows,
                groups=group_by_place([r.location for r in rows]) if query.group else [],
                views=VIEW_CHOICES,
                columns=(
                    ("company", "Company"),
                    ("title", "Role"),
                    ("status", "Status"),
                    ("applied", "Applied"),
                    ("follow_up", "Follow-up"),
                ),
            ),
        )

    @app.get("/tracker.csv")
    def tracker_csv(
        request: Request,
        view: str | None = None,
        q: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
    ) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        query = clean_query(view, q, status or [], None, None)
        with open_session() as session:
            text = csv_for(session, query, utcnow())
        return Response(
            text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="applications.csv"'},
        )

    @app.post("/applications/{application_id}")
    async def update_application(request: Request, application_id: JobId) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        error: str | None = None
        with open_session() as session:
            application = session.get(Application, application_id)
            if application is None:
                return reaction_page(request, "note.html", message="That application is gone.")
            try:
                # check every input first, so a bad date cannot leave the status half changed
                due = follow_up_from_text(form["follow_up"][0]) if "follow_up" in form else None
                if "status" in form and form["status"][0] not in dict(STATUS_CHOICES):
                    raise TrackerError(f"Unknown status {form['status'][0]!r}.")
                if "status" in form:
                    change_status(session, application.job_id, form["status"][0])
                if "follow_up" in form:
                    set_follow_up(session, application_id, due)
                if "next_step" in form:
                    set_next_step(session, application_id, form["next_step"][0])
            except TrackerError as problem:
                session.rollback()
                error = str(problem)
            except ValueError:  # a date that is not YYYY-MM-DD
                session.rollback()
                error = "That date was not understood. Use YYYY-MM-DD."
            rows = gather_tracker(session, TrackerQuery(), utcnow())
            row = next((r for r in rows if r.application_id == application_id), None)
        if request.headers.get("hx-request") != "true" or row is None:
            return RedirectResponse("/tracker", status_code=303)
        return templates.TemplateResponse(
            request, "tracker_row.html", tracker_context(request, row=row, error=error)
        )

    def people_page(
        request: Request, path: str, template: str, session: Session, **extra: Any
    ) -> Response:
        title = PAGES[path][0]
        return templates.TemplateResponse(
            request,
            template,
            {
                "tabs": TABS,
                "current": path,
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": title,
                "heading": title,
                "note": None,
                **extra,
            },
        )

    def contacts_page(
        request: Request, session: Session, text: str = "", note: str | None = None
    ) -> Response:
        return people_page(
            request, "/contacts", "contacts.html", session,
            contacts=gather_contacts(session, text), text=text, note=note,
        )  # fmt: skip

    def interactions_page(
        request: Request, session: Session, person_id: int | None = None, note: str | None = None
    ) -> Response:
        return people_page(
            request, "/interactions", "interactions.html", session,
            interactions=gather_interactions(session, utcnow()), people=people_choices(session),
            channels=CHANNELS, person_id=person_id, note=note,
        )  # fmt: skip

    @app.get("/contacts", response_class=HTMLResponse)
    def contacts_tab(request: Request, q: str | None = None) -> Response:
        if open_session is None or not (opened["ok"] or check_database()):
            return tab("/contacts")(request)
        opened["ok"] = True
        with open_session() as session:
            return contacts_page(request, session, (q or "")[:100])

    @app.post("/people/{person_id}")
    async def edit_person(request: Request, person_id: JobId) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        with open_session() as session:
            try:
                set_own_words(session, person_id, form.get("how_known", [""])[0])
            except UnknownPerson:
                return contacts_page(request, session, note="That person is gone.")
            except InteractionError as problem:
                session.rollback()
                return contacts_page(request, session, note=str(problem))
        return RedirectResponse("/contacts", status_code=303)

    @app.get("/interactions", response_class=HTMLResponse)
    def interactions_tab(request: Request, person: int | None = None) -> Response:
        if open_session is None or not (opened["ok"] or check_database()):
            return tab("/interactions")(request)
        opened["ok"] = True
        with open_session() as session:
            return interactions_page(request, session, person)

    @app.post("/interactions")
    async def add_interaction(request: Request) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        raw = form.get("person", [""])[0]
        person_id = int(raw) if raw.isdigit() and 0 < int(raw) < 2**63 else None
        with open_session() as session:
            try:
                if person_id is None:
                    raise InteractionError("Choose who you contacted.")
                due = follow_up_from_text(form.get("follow_up", [""])[0])
                log_interaction(
                    session, person_id, form.get("channel", ["other"])[0],
                    form.get("summary", [""])[0], follow_up_due=due,
                )  # fmt: skip
            except UnknownPerson:
                return interactions_page(request, session, note="That person is gone.")
            except InteractionError as problem:
                session.rollback()
                return interactions_page(request, session, person_id, str(problem))
            except ValueError:  # a date that is not YYYY-MM-DD
                session.rollback()
                return interactions_page(
                    request, session, person_id, "That date was not understood. Use YYYY-MM-DD."
                )
        return RedirectResponse("/interactions", status_code=303)

    @app.post("/interactions/{interaction_id}/done")
    async def finish_follow_up(request: Request, interaction_id: JobId) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        with open_session() as session:
            try:
                set_follow_up_done(session, interaction_id, form.get("done", ["1"])[0] != "0")
            except InteractionError as problem:
                session.rollback()
                return interactions_page(request, session, note=str(problem))
        return RedirectResponse("/interactions", status_code=303)

    def conversation_page(
        request: Request, session: Session, job_id: int | None, note: str | None = None
    ) -> Response:
        job = session.get(Job, job_id) if job_id is not None else None
        return templates.TemplateResponse(
            request,
            "conversation.html",
            {
                "tabs": TABS,
                "current": "/today",
                "csrf": sessions.get(request.cookies.get(SESSION_COOKIE, ""), ""),
                "title": "Ask a question",
                "heading": "Ask a question",
                "messages": recent_messages(session, job_id),
                "job": job,
                "job_id": job.id if job is not None else None,
                "note": note,
                "ready": open_router is not None,
                "proposals": pending_proposals(session),
                "declined": declined_proposals(session),
            },
        )

    @app.get("/conversation", response_class=HTMLResponse)
    def conversation(request: Request, job: int | None = None) -> Response:
        if open_session is None:
            return tab("/today")(request)
        opened["ok"] = opened["ok"] or check_database()
        if not opened["ok"]:
            return tab("/today")(request)
        with open_session() as session:
            return conversation_page(request, session, job)

    @app.post("/conversation")
    async def ask_question(request: Request) -> Response:
        if open_session is None:
            return HTMLResponse("No data.", status_code=404)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        question = form.get("question", [""])[0]
        raw_job = form.get("job", [""])[0]
        job_id = int(raw_job) if raw_job.isdigit() and int(raw_job) < 2**63 else None
        with open_session() as session:
            if open_router is None:
                return conversation_page(request, session, job_id, "No model is set up yet.")
            try:
                router, routes = open_router()
            except NoRoute:
                return conversation_page(
                    request,
                    session,
                    job_id,
                    "No model is set up for questions yet. Run `hub models add` in the terminal.",
                )
            doc = load_profile_doc()
            name, aliases = (doc.name, doc.aliases) if doc else ("", [])
            context = build_context(session, load_rubric(), name, aliases, job_id)
            answer = ask(session, router, routes, question, context, name, aliases, job_id)
            if answer.ok:
                return RedirectResponse(
                    "/conversation" + (f"?job={job_id}" if job_id else ""), status_code=303
                )
            return conversation_page(request, session, job_id, answer.text)

    @app.post("/proposals/{proposal_id}/{decision}")
    async def decide_proposal(request: Request, proposal_id: JobId, decision: str) -> Response:
        if open_session is None or decision not in ("accept", "reject", "reopen"):
            return HTMLResponse("Not found.", status_code=404)
        with open_session() as session:
            try:
                if decision == "accept":
                    accept_proposal(session, proposal_id)
                elif decision == "reject":
                    reject_proposal(session, proposal_id)
                else:
                    reopen_proposal(session, proposal_id)
            except ProposalError as problem:
                return conversation_page(request, session, None, str(problem))
        return RedirectResponse("/conversation", status_code=303)

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


def _when(moment: datetime | None) -> str | None:
    """Format a time for the page, or None."""
    return f"{moment:%a %b %d, %H:%M}" if moment else None


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
