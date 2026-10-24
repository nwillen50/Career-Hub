"""Follow one company from its own careers page (CH-188).

`hub targets add <name> --careers-url <url>` fetches that one page once (no crawling, `robots.txt`
honored, LinkedIn and Handshake never) and looks for an embedded job board of a system Career Hub
can read: Greenhouse, Lever, or Ashby. That gives the exact board name instead of a guess. A page
on a system we cannot read (Workday and the like) is recorded on the target as "board not
supported", so the gap shows on System status; jobs there can still be pasted one by one. A board
that is found becomes a *proposed* source, like every other source: nothing is fetched from it
until the user approves it. Without a careers page, the company's name is used to guess boards
(`find_boards`), as before.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from careerhub.core.names import company_key
from careerhub.modules.jobs.find_boards import BOARD_URLS, BoardHit, find_boards
from careerhub.modules.jobs.sources import propose_source
from careerhub.modules.jobs.targets import Target

USER_AGENT = "CareerHub/1.0 (personal job search tool; reads one careers page)"
TIMEOUT_SECONDS = 15.0
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3
#: Sites Career Hub never requests (PRD: no LinkedIn or Handshake access).
NEVER_HOSTS = ("linkedin.com", "handshake.com", "joinhandshake.com")

_TOKEN = r"([A-Za-z0-9][A-Za-z0-9_-]{1,59})"
#: Where an embedded board shows up in a page: a link, an iframe, or a script.
_GREENHOUSE_EMBED = rf"(?:job-)?boards\.greenhouse\.io/embed/job_board(?:/js)?\?for={_TOKEN}"
_BOARD_PATTERNS = (
    ("greenhouse", re.compile(_GREENHOUSE_EMBED)),
    ("greenhouse", re.compile(rf"(?:job-)?boards\.greenhouse\.io/{_TOKEN}")),
    ("lever", re.compile(rf"jobs\.lever\.co/{_TOKEN}")),
    ("ashby", re.compile(rf"jobs\.ashbyhq\.com/{_TOKEN}")),
)  # fmt: skip
#: Path words that are part of a board's address but are not a company's board name.
_NOT_A_BOARD = frozenset({"embed", "v1", "api", "jobs", "job_board", "static", "assets"})
#: Systems we recognise but cannot read, by a mark in the page.
_UNSUPPORTED = (
    ("myworkdayjobs.com", "Workday"),
    ("ats.rippling.com", "Rippling"),
    ("apply.workable.com", "Workable"),
    ("icims.com", "iCIMS"),
    ("smartrecruiters.com", "SmartRecruiters"),
    ("taleo.net", "Taleo"),
    ("jobvite.com", "Jobvite"),
)


@dataclass(frozen=True)
class PageResult:
    """What one careers page said: a readable board, an unreadable system, or nothing."""

    board: BoardHit | None = None
    unsupported: str | None = None
    problem: str | None = None


def allowed_address(url: str) -> str | None:
    """Return why `url` may not be requested, or None when it may (https only; no LinkedIn)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return "A careers page address must start with https://"
    if any(host == h or host.endswith("." + h) for h in NEVER_HOSTS):
        return "Career Hub never requests LinkedIn or Handshake."
    if host == "localhost" or re.fullmatch(r"[\d.]+|[\da-f:]+", host):
        return "Use the company's web address, not an IP address or this machine."
    return None


def find_board_in_page(html: str) -> PageResult:
    """Look in `html` for an embedded board of a supported system, then for an unsupported one."""
    for kind, pattern in _BOARD_PATTERNS:
        for match in pattern.finditer(html):
            token = match.group(1)
            if token.lower() not in _NOT_A_BOARD:
                return PageResult(board=BoardHit(kind, token, True))
    lowered = html.lower()
    for mark, name in _UNSUPPORTED:
        if mark in lowered:
            return PageResult(unsupported=name)
    return PageResult()


def _robots_allow(url: str, client: httpx.Client) -> bool | str:
    """Return True when `robots.txt` allows `url`, else the reason it was not fetched."""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        reply = client.get(robots_url)
    except httpx.HTTPError:
        return "robots.txt could not be read, so the page was not fetched."
    if reply.status_code >= 500:
        return "robots.txt could not be read, so the page was not fetched."
    if reply.status_code >= 400:
        return True  # no robots.txt: nothing is disallowed
    parser = robotparser.RobotFileParser()
    parser.parse(reply.text.splitlines())
    if parser.can_fetch(USER_AGENT, url):
        return True
    return "The site's robots.txt asks programs not to fetch that page, so it was not fetched."


def read_careers_page(url: str, client: httpx.Client) -> PageResult:
    """Fetch `url` once (after checking robots.txt) and look for a job board in it."""
    why = allowed_address(url)
    if why:
        return PageResult(problem=why)
    allowed = _robots_allow(url, client)
    if allowed is not True:
        return PageResult(problem=str(allowed))
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            reply = client.get(current)
        except httpx.HTTPError:
            return PageResult(problem="The careers page could not be reached.")
        if reply.status_code in (301, 302, 303, 307, 308) and reply.headers.get("location"):
            current = str(httpx.URL(current).join(reply.headers["location"]))
            if urlsplit(current).netloc != urlsplit(url).netloc:
                return PageResult(problem="The page redirected to another site, so it was skipped.")
            continue
        if reply.status_code != 200:
            return PageResult(problem=f"The careers page answered with {reply.status_code}.")
        return find_board_in_page(reply.text[:MAX_BYTES])
    return PageResult(problem="The careers page redirected too many times.")


def new_client() -> httpx.Client:
    """Return the HTTP client used for careers pages (identifies itself, bounded waits)."""
    return httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def track_company(
    session: Session,
    targets: list[Target],
    name: str,
    careers_url: str | None = None,
    client: httpx.Client | None = None,
    aliases: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[str]:
    """Follow `name` (adding it if new), look for its board, and return lines saying what happened.

    The name goes through the alias rules (`aliases`, CH-111), so "Old Name" follows "Name Now".
    """
    http = client or new_client()
    key = company_key(name, aliases)
    if not key:
        return ["That company name is empty."]
    target = next((t for t in targets if company_key(t.name, aliases) == key), None)
    lines = []
    if target is None:
        target = Target(name=" ".join(name.split()), enabled=True)
        targets.append(target)
        lines.append(f"Now following {target.name}.")
    else:
        target.enabled = True
        lines.append(f"{target.name} was already in your list; it is followed now.")
    if target.board:
        return [*lines, f"Its board is already known ({target.board_kind}:{target.board})."]
    if careers_url:
        result = read_careers_page(careers_url, http)
        if result.board:
            hit = result.board
            target.board_kind, target.board = hit.kind, hit.token  # type: ignore[assignment]
            target.unsupported_ats = None
            propose_source(
                session, hit.kind, target.name, BOARD_URLS[hit.kind].format(token=hit.token),
                {"board": hit.token},
            )  # fmt: skip
            return [*lines, f"Found a {hit.kind} board {hit.token!r} on its careers page "
                    "(proposed; approve it with hub sources)."]  # fmt: skip
        if result.unsupported:
            target.unsupported_ats = result.unsupported
            return [*lines, f"Board not supported: {result.unsupported}. You can still paste "
                    "single jobs with hub add-job."]  # fmt: skip
        lines.append(result.problem or "No supported job board was embedded in that page.")
        lines.append("Trying likely board names instead.")
    kwargs = {"sleep": sleep} if sleep else {}
    report = find_boards(session, [target], http, **kwargs)
    for _, hit in report.found:
        lines.append(
            f"Found a {hit.kind} board {hit.token!r} (proposed; approve it with hub sources)."
        )
    for _, hit in report.uncertain:
        lines.append(f"Unsure: a {hit.kind} board {hit.token!r} answered but does not name them.")
    if report.missed:
        lines.append("No board found. You can still paste single jobs with hub add-job.")
    if report.stopped:
        lines.append(report.stopped)
    return lines
